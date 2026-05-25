#!/usr/bin/env python3
"""
Process papers for language detection: abstract → HTML → PDF fallback.

For each paper in the input JSONL:
  1. Abstract is always scanned for language mentions.
  2. HTML version is fetched from arXiv and scanned section-by-section.
  3. If HTML is unavailable, the PDF is downloaded and its full text is scanned.
  4. If neither HTML nor PDF is available, the paper is logged in the warnings file.

All detected languages are documented per-section in the output JSONL, with the
source field indicating where each detection came from ("abstract", "html", "pdf").

Usage:
    python scripts/process_papers.py --input data/raw/extracted_papers_metadata/arxiv_papers_...jsonl
    python scripts/process_papers.py --input <file.jsonl> --workers 8
    python scripts/process_papers.py --input <file.jsonl> --output-dir data/processed
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from langtrend.text_cleaning import clean_paper_text_for_language_screening, detect_languages_in_text
from langtrend.html_processor import recheck_languages_from_html
from langtrend.pdf_processor import PDFProcessor

_DEFAULT_LANG_DATA = Path(__file__).parent.parent / "data/processed/language_data.json"
_DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "data/processed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_papers(jsonl_path: Path) -> list[dict]:
    papers = []
    with jsonl_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                try:
                    papers.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return papers


def load_language_data(path: Path) -> tuple[dict[int, set[str]], set[str]]:
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    lang_classes = {int(k): set(v) for k, v in data["lang_classes"].items()}
    languages_to_ignore = set(data["languages_to_ignore"])
    return lang_classes, languages_to_ignore


def _download_pdf(pdf_url: str, pdf_dir: Path, paper_id: str) -> Path | None:
    """Download a PDF if not already cached. Returns path or None on failure."""
    filename = paper_id.split("/")[-1]
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    pdf_path = pdf_dir / filename
    if pdf_path.exists():
        return pdf_path
    try:
        resp = requests.get(pdf_url, stream=True, timeout=60)
        resp.raise_for_status()
        with pdf_path.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
        return pdf_path
    except Exception:
        return None


def _build_detections(raw_languages: list[str], lang_classes: dict[int, set[str]]) -> list[dict]:
    """Attach class IDs to a list of detected language strings."""
    result = []
    for language in raw_languages:
        for class_id, langs in lang_classes.items():
            if language in langs:
                result.append({"language": language, "class": class_id})
                break
    return result


def _detect_in_text(text: str, lang_classes: dict, languages_to_ignore: set, paper_id: str) -> list[dict]:
    cleaned_blocks, _ = clean_paper_text_for_language_screening(text)
    if not cleaned_blocks:
        return []
    raw = detect_languages_in_text(cleaned_blocks, lang_classes, languages_to_ignore, paper_id=paper_id)
    return _build_detections(raw, lang_classes)


# ---------------------------------------------------------------------------
# Per-paper worker
# ---------------------------------------------------------------------------

def _process_single_paper(
    paper: dict,
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    pdf_dir: Path,
    html_cache_dir: Path,
    pdf_text_dir: Path,
    pdf_cache_dir: Path,
) -> dict:
    paper_id = paper.get("id", "unknown")
    record: dict = {
        "paper_id": paper_id,
        "paper": paper,
        "sources_checked": [],
        "sections": {},
        "warnings": [],
    }

    # 1. Abstract (always scanned)
    abstract = paper.get("abstract", "")
    if abstract:
        detections = _detect_in_text(abstract, lang_classes, languages_to_ignore, paper_id)
        record["sources_checked"].append("abstract")
        if detections:
            record["sections"]["abstract"] = {"source": "abstract", "detected_languages": detections}

    # 2. HTML extraction
    html_cache: dict | None = None
    try:
        html_cache = recheck_languages_from_html(
            paper,
            lang_classes,
            languages_to_ignore,
            out_dir=html_cache_dir,
        )
        if html_cache is not None:
            record["sources_checked"].append("html")
            for section_title, languages in html_cache.items():
                if not languages:
                    continue
                detections = _build_detections(languages, lang_classes)
                if detections:
                    record["sections"][section_title] = {
                        "source": "html",
                        "detected_languages": detections,
                    }
    except Exception as exc:
        html_cache = None
        record["warnings"].append({"step": "html", "error": str(exc)})

    # 3. PDF fallback — only when HTML returned nothing (unavailable or empty)
    html_unavailable = html_cache is None or len(html_cache) == 0
    if html_unavailable:
        pdf_url = paper.get("pdf_url")
        if pdf_url:
            pdf_path = _download_pdf(pdf_url, pdf_dir, paper_id)
            if pdf_path:
                record["sources_checked"].append("pdf")
                try:
                    processor = PDFProcessor(input_dir=str(pdf_dir), output_dir=str(pdf_text_dir))
                    raw_text, _ = processor.extract_text(pdf_path)
                    if raw_text:
                        cleaned_text = processor.clean_text(raw_text)
                        detections = _detect_in_text(cleaned_text, lang_classes, languages_to_ignore, paper_id)
                        if detections:
                            record["sections"]["pdf_full_text"] = {
                                "source": "pdf",
                                "detected_languages": detections,
                            }
                        # Save to pdf_cache
                        safe_id = str(paper_id).split("/")[-1]
                        pdf_cache_path = pdf_cache_dir / f"{safe_id}.json"
                        pdf_cache_dir.mkdir(parents=True, exist_ok=True)
                        with pdf_cache_path.open("w", encoding="utf-8") as fh:
                            json.dump({
                                "paper_id": paper_id,
                                "text": raw_text,
                                "cleaned_text": cleaned_text,
                                "detected_languages": detections,
                            }, fh, ensure_ascii=False, indent=2)
                except Exception as exc:
                    record["warnings"].append({"step": "pdf_processing", "error": str(exc)})
            else:
                record["warnings"].append({
                    "step": "pdf_download",
                    "error": f"Failed to download PDF from {pdf_url}",
                })
                record["sources_checked"].append("pdf_unavailable")
        else:
            record["warnings"].append({"step": "pdf", "error": "No PDF URL available"})
            record["sources_checked"].append("pdf_unavailable")

    return record


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def process_papers(
    papers: list[dict],
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    output_jsonl: Path,
    warnings_file: Path,
    pdf_dir: Path,
    html_cache_dir: Path,
    pdf_text_dir: Path,
    pdf_cache_dir: Path,
    max_workers: int = 4,
) -> dict:
    for d in [pdf_dir, html_cache_dir, pdf_text_dir, pdf_cache_dir]:
        d.mkdir(parents=True, exist_ok=True)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    all_warnings: list[dict] = []
    results: list[dict] = []
    stats = {
        "total_papers": len(papers),
        "papers_with_detections": 0,
        "total_detections": 0,
        "failed_papers": 0,
        "sources": {"abstract": 0, "html": 0, "pdf": 0, "pdf_unavailable": 0},
    }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_single_paper,
                paper,
                lang_classes,
                languages_to_ignore,
                pdf_dir,
                html_cache_dir,
                pdf_text_dir,
                pdf_cache_dir,
            ): paper
            for paper in papers
        }

        with tqdm(as_completed(futures), total=len(futures), desc="Processing papers") as pbar:
            for future in pbar:
                try:
                    record = future.result()
                    for source in record.get("sources_checked", []):
                        if source in stats["sources"]:
                            stats["sources"][source] += 1

                    if record.get("warnings"):
                        all_warnings.extend(record["warnings"])

                    if record.get("sections"):
                        results.append(record)
                        stats["papers_with_detections"] += 1
                        for sec in record["sections"].values():
                            stats["total_detections"] += len(sec.get("detected_languages", []))

                except Exception as exc:
                    paper = futures[future]
                    all_warnings.append({
                        "paper_id": paper.get("id", "unknown"),
                        "error": str(exc),
                        "timestamp": datetime.now().isoformat(),
                    })
                    stats["failed_papers"] += 1

    with output_jsonl.open("w", encoding="utf-8") as fp:
        for record in results:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

    if all_warnings:
        with warnings_file.open("w", encoding="utf-8") as fp:
            json.dump(all_warnings, fp, ensure_ascii=False, indent=2)
        print(f"Warnings saved to {warnings_file}")

    print(f"\nTotal papers:            {stats['total_papers']}")
    print(f"Papers with detections:  {stats['papers_with_detections']}")
    print(f"Total detections:        {stats['total_detections']}")
    print(f"Failed:                  {stats['failed_papers']}")
    print(f"Sources — abstract:{stats['sources']['abstract']}  html:{stats['sources']['html']}  pdf:{stats['sources']['pdf']}  pdf_unavailable:{stats['sources']['pdf_unavailable']}")
    print(f"Output: {output_jsonl}")

    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process papers for language detection (abstract → HTML → PDF fallback)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Input JSONL file produced by fetch_arxiv_metadata.py",
    )
    parser.add_argument(
        "--language-data",
        type=Path,
        default=_DEFAULT_LANG_DATA,
        help=f"language_data.json produced by extract_language_data.py (default: {_DEFAULT_LANG_DATA})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"Directory for output files (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument("--workers", type=int, default=4, help="Parallel worker threads (default: 4)")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if not args.language_data.exists():
        print(
            f"Error: language data file not found: {args.language_data}\n"
            "Run scripts/extract_language_data.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    lang_classes, languages_to_ignore = load_language_data(args.language_data)
    print(f"Loaded {sum(len(v) for v in lang_classes.values())} language entries across {len(lang_classes)} classes")

    papers = load_papers(args.input)
    print(f"Loaded {len(papers)} papers from {args.input}")

    stem = args.input.stem

    process_papers(
        papers=papers,
        lang_classes=lang_classes,
        languages_to_ignore=languages_to_ignore,
        output_jsonl=args.output_dir / f"{stem}_detected.jsonl",
        warnings_file=args.output_dir / f"{stem}_warnings.json",
        pdf_dir=Path(__file__).parent.parent / "data/raw/pdfs",
        html_cache_dir=args.output_dir / "html_cache",
        pdf_text_dir=args.output_dir / "pdf_text",
        pdf_cache_dir=args.output_dir / "pdf_cache",
        max_workers=args.workers,
    )


if __name__ == "__main__":
    main()
