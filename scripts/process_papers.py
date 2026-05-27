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
    python scripts/process_papers.py --input <file.jsonl> --output-dir data/processed/weeks/20260518_to_20260525
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from pathlib import Path

import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from langtrend.manifest import build_detections
from langtrend.text_cleaning import clean_paper_text_for_language_screening, detect_languages_in_text, trim_pdf_text_to_body
from langtrend.html_processor import recheck_languages_from_html
from langtrend.pdf_processor import PDFProcessor

_DEFAULT_LANG_DATA = Path(__file__).parent.parent / "data/processed/language_data.json"
_DEFAULT_PROCESSED_DIR = Path(__file__).parent.parent / "data/processed"


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


def load_language_data(path: Path) -> tuple[dict[int, set[str]], set[str], dict[str, str]]:
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    lang_classes = {int(k): set(v) for k, v in data["lang_classes"].items()}
    languages_to_ignore = set(data["languages_to_ignore"])
    possible_false_positive_languages: dict[str, str] = data.get("possible_false_positive_languages", {})
    return lang_classes, languages_to_ignore, possible_false_positive_languages


_PDF_MAX_BYTES = 30 * 1024 * 1024  # skip PDFs larger than 30 MB

_PDF_DOWNLOAD_TIMEOUT = 180  # wall-clock cap for entire PDF download
_PDF_LOG_EVERY = 256 * 1024

def _download_pdf(pdf_url: str, pdf_dir: Path, paper_id: str) -> Path | None:
    """Download a PDF into a per-paper subdirectory. Returns path or None on failure."""
    import time as _time
    safe_id = paper_id.split("/")[-1]
    paper_pdf_dir = pdf_dir / safe_id
    paper_pdf_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_id if safe_id.lower().endswith(".pdf") else f"{safe_id}.pdf"
    pdf_path = paper_pdf_dir / filename
    if pdf_path.exists():
        tqdm.write(f"    [{paper_id}] PDF already on disk ({pdf_path.stat().st_size // 1024}KB)")
        return pdf_path
    tqdm.write(f"    [{paper_id}] PDF GET {pdf_url}")
    t0 = _time.monotonic()
    try:
        resp = requests.get(pdf_url, stream=True, timeout=(10, 30))
        resp.raise_for_status()
        tqdm.write(f"    [{paper_id}] PDF response {resp.status_code}, streaming…")
        downloaded = 0
        last_logged = 0
        with pdf_path.open("wb") as fh:
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        downloaded += len(chunk)
                        fh.write(chunk)
                        if downloaded - last_logged >= _PDF_LOG_EVERY:
                            elapsed = _time.monotonic() - t0
                            tqdm.write(f"    [{paper_id}] PDF downloading… {downloaded // 1024}KB in {elapsed:.1f}s")
                            last_logged = downloaded
                        if downloaded > _PDF_MAX_BYTES:
                            tqdm.write(f"    [{paper_id}] PDF too large (>{_PDF_MAX_BYTES // (1024*1024)}MB) — skipping")
                            return None
                    if _time.monotonic() - t0 > _PDF_DOWNLOAD_TIMEOUT:
                        elapsed = _time.monotonic() - t0
                        tqdm.write(f"    [{paper_id}] PDF download wall-clock timeout after {elapsed:.1f}s at {downloaded // 1024}KB")
                        return None
            except Exception as chunk_err:
                elapsed = _time.monotonic() - t0
                tqdm.write(f"    [{paper_id}] PDF chunk error after {elapsed:.1f}s at {downloaded // 1024}KB: {type(chunk_err).__name__}: {chunk_err}")
                if pdf_path.exists():
                    pdf_path.unlink(missing_ok=True)
                return None
        elapsed = _time.monotonic() - t0
        tqdm.write(f"    [{paper_id}] PDF downloaded {downloaded // 1024}KB in {elapsed:.1f}s → {pdf_path}")
        return pdf_path
    except Exception as e:
        elapsed = _time.monotonic() - t0
        tqdm.write(f"    [{paper_id}] PDF fetch failed after {elapsed:.1f}s: {type(e).__name__}: {e}")
        if pdf_path.exists():
            pdf_path.unlink(missing_ok=True)
        return None


def _detect_in_text(
    text: str,
    lang_classes: dict,
    languages_to_ignore: set,
    paper_id: str,
    possible_false_positive_languages: dict[str, str] | None = None,
) -> list[dict]:
    cleaned_blocks, _ = clean_paper_text_for_language_screening(text, _label=paper_id)
    if not cleaned_blocks:
        return []
    raw = detect_languages_in_text(cleaned_blocks, lang_classes, languages_to_ignore, paper_id=paper_id)
    return build_detections(raw, lang_classes, possible_false_positive_languages)


# ---------------------------------------------------------------------------
# Per-paper worker
# ---------------------------------------------------------------------------

def _process_single_paper(
    paper: dict,
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    possible_false_positive_languages: dict[str, str],
    pdf_dir: Path,
    html_cache_dir: Path,
    pdf_cache_dir: Path,
) -> dict:
    import time as _time
    t_paper = _time.monotonic()

    paper_id = paper.get("id", "unknown")
    tqdm.write(f"  [{paper_id}] START")
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
        detections = _detect_in_text(abstract, lang_classes, languages_to_ignore, paper_id, possible_false_positive_languages)
        record["sources_checked"].append("abstract")
        if detections:
            record["sections"]["abstract"] = {"source": "abstract", "detected_languages": detections}

    # 2. HTML extraction
    html_cache: dict | None = None
    is_html_complete = False
    t_html = _time.monotonic()
    try:
        html_cache, is_html_complete = recheck_languages_from_html(
            paper,
            lang_classes,
            languages_to_ignore,
            out_dir=html_cache_dir,
        )
        if html_cache is not None:
            tqdm.write(f"  [{paper_id}] HTML ok ({len(html_cache)} sections, complete={is_html_complete}) in {_time.monotonic()-t_html:.1f}s")
            if is_html_complete:
                record["sources_checked"].append("html")
                for section_title, languages in html_cache.items():
                    if not languages:
                        continue
                    detections = build_detections(languages, lang_classes, possible_false_positive_languages)
                    if detections:
                        record["sections"][section_title] = {
                            "source": "html",
                            "detected_languages": detections,
                        }
        else:
            tqdm.write(f"  [{paper_id}] HTML unavailable in {_time.monotonic()-t_html:.1f}s")
    except Exception as exc:
        html_cache = None
        is_html_complete = False
        tqdm.write(f"  [{paper_id}] HTML error after {_time.monotonic()-t_html:.1f}s: {type(exc).__name__}: {exc}")
        record["warnings"].append({"step": "html", "error": str(exc)})

    # Preserve partial HTML detections for use as a last resort if PDF also fails
    partial_html_cache = html_cache if (not is_html_complete and html_cache) else None

    # 3. PDF fallback — when HTML unavailable, empty, or incomplete (stalled download)
    html_unavailable = html_cache is None or not is_html_complete or len(html_cache) == 0
    if html_unavailable:
        safe_id = str(paper_id).split("/")[-1]
        pdf_cache_path = pdf_cache_dir / f"{safe_id}.json"
        if pdf_cache_path.exists():
            tqdm.write(f"  [{paper_id}] PDF cache hit")
            record["sources_checked"].append("pdf")
            try:
                with pdf_cache_path.open("r", encoding="utf-8") as fh:
                    pdf_cached = json.load(fh)
                detections = pdf_cached.get("detected_languages", [])
                if detections:
                    record["sections"]["pdf_full_text"] = {
                        "source": "pdf",
                        "detected_languages": detections,
                    }
            except Exception as exc:
                tqdm.write(f"  [{paper_id}] PDF cache read error: {type(exc).__name__}: {exc}")
                record["warnings"].append({"step": "pdf_cache_read", "error": str(exc)})
            tqdm.write(f"  [{paper_id}] DONE in {_time.monotonic()-t_paper:.1f}s")
            return record

        pdf_url = paper.get("pdf_url")
        if pdf_url:
            t_pdf = _time.monotonic()
            pdf_path = _download_pdf(pdf_url, pdf_dir, paper_id)
            tqdm.write(f"  [{paper_id}] PDF download {'ok' if pdf_path else 'failed'} in {_time.monotonic()-t_pdf:.1f}s")
            if pdf_path:
                record["sources_checked"].append("pdf")
                try:
                    t_extract = _time.monotonic()
                    processor = PDFProcessor(input_dir=str(pdf_path.parent), output_dir=str(pdf_path.parent))
                    raw_text, _ = processor.extract_text(pdf_path)
                    tqdm.write(f"  [{paper_id}] PDF text extracted ({len(raw_text)} chars) in {_time.monotonic()-t_extract:.1f}s")
                    if raw_text:
                        cleaned_text = processor.clean_text(raw_text)
                        body_text = trim_pdf_text_to_body(cleaned_text)
                        screened_blocks, _ = clean_paper_text_for_language_screening(body_text, _label=paper_id)
                        raw_langs = detect_languages_in_text(screened_blocks, lang_classes, languages_to_ignore, paper_id=paper_id)
                        detections = build_detections(raw_langs, lang_classes, possible_false_positive_languages)
                        if detections:
                            record["sections"]["pdf_full_text"] = {
                                "source": "pdf",
                                "detected_languages": detections,
                            }
                        pdf_cache_dir.mkdir(parents=True, exist_ok=True)
                        with pdf_cache_path.open("w", encoding="utf-8") as fh:
                            json.dump({
                                "paper_id": paper_id,
                                "text": raw_text,
                                "cleaned_text": cleaned_text,
                                "body_text": body_text,
                                "screened_text": "\n\n".join(screened_blocks),
                                "detected_languages": detections,
                            }, fh, ensure_ascii=False, indent=2)
                except Exception as exc:
                    tqdm.write(f"  [{paper_id}] PDF processing error: {type(exc).__name__}: {exc}")
                    record["warnings"].append({"step": "pdf_processing", "error": str(exc)})
            else:
                record["warnings"].append({"step": "pdf_download", "error": f"Failed to download PDF from {pdf_url}"})
                record["sources_checked"].append("pdf_unavailable")
        else:
            record["warnings"].append({"step": "pdf", "error": "No PDF URL available"})
            record["sources_checked"].append("pdf_unavailable")

        # Last resort: partial HTML (stalled download) when PDF is also unavailable
        pdf_succeeded = "pdf" in record["sources_checked"]
        if not pdf_succeeded and partial_html_cache:
            tqdm.write(f"  [{paper_id}] using partial HTML as last resort")
            record["sources_checked"].append("html_partial")
            record["warnings"].append({
                "step": "html_partial",
                "error": "HTML download stalled mid-transfer — only partial content analyzed",
            })
            for section_title, languages in partial_html_cache.items():
                if not languages:
                    continue
                detections = build_detections(languages, lang_classes, possible_false_positive_languages)
                if detections:
                    record["sections"][section_title] = {"source": "html_partial", "detected_languages": detections}

    tqdm.write(f"  [{paper_id}] DONE in {_time.monotonic()-t_paper:.1f}s")
    return record


# ---------------------------------------------------------------------------
# Cache-only reprocessing (skip HTML/PDF downloads; re-run cleaning+detection)
# ---------------------------------------------------------------------------

def _reprocess_single_paper(
    paper: dict,
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    possible_false_positive_languages: dict[str, str],
    html_cache_dir: Path,
    pdf_cache_dir: Path,
) -> dict:
    """Re-run text cleaning + language detection on cached extractions only."""
    import time as _time
    t_paper = _time.monotonic()
    paper_id = paper.get("id", "unknown")
    safe_id = str(paper_id).split("/")[-1]

    record: dict = {
        "paper_id": paper_id,
        "paper": paper,
        "sources_checked": [],
        "sections": {},
        "warnings": [],
    }

    # 1. Abstract (always re-scanned from paper metadata)
    abstract = paper.get("abstract", "")
    if abstract:
        detections = _detect_in_text(abstract, lang_classes, languages_to_ignore, paper_id, possible_false_positive_languages)
        record["sources_checked"].append("abstract")
        if detections:
            record["sections"]["abstract"] = {"source": "abstract", "detected_languages": detections}

    # 2. HTML cache
    html_cache_path = html_cache_dir / f"{safe_id}.json"
    is_html_complete = False
    html_detections: dict[str, list[str]] = {}

    if html_cache_path.exists():
        try:
            with html_cache_path.open("r", encoding="utf-8") as fh:
                html_cached = json.load(fh)
            is_html_complete = html_cached.get("_complete", True)

            updated_cache: dict = {"_complete": is_html_complete}
            for section_title, section_data in html_cached.items():
                if section_title.startswith("_"):
                    continue
                text = section_data.get("text", "") if isinstance(section_data, dict) else ""
                if not text:
                    updated_cache[section_title] = section_data
                    continue
                cleaned_blocks, _ = clean_paper_text_for_language_screening(text, _label=paper_id)
                cleaned_text = "\n\n".join(cleaned_blocks)
                detected: list[str] = []
                if cleaned_blocks:
                    detected = detect_languages_in_text(
                        [section_title] + cleaned_blocks, lang_classes, languages_to_ignore, paper_id=paper_id
                    )
                updated_cache[section_title] = {
                    "text": text,
                    "cleaned_text": cleaned_text,
                    "detected": detected,
                }
                if detected:
                    html_detections[section_title] = detected

            with html_cache_path.open("w", encoding="utf-8") as fh:
                json.dump(updated_cache, fh, ensure_ascii=False, indent=2)

            if is_html_complete:
                record["sources_checked"].append("html")
                for section_title, languages in html_detections.items():
                    dets = build_detections(languages, lang_classes, possible_false_positive_languages)
                    if dets:
                        record["sections"][section_title] = {"source": "html", "detected_languages": dets}
        except Exception as exc:
            tqdm.write(f"  [{paper_id}] HTML cache reprocess error: {type(exc).__name__}: {exc}")
            record["warnings"].append({"step": "html_reprocess", "error": str(exc)})
            is_html_complete = False

    # 3. PDF cache — same fallback condition as _process_single_paper
    html_unavailable = not html_cache_path.exists() or not is_html_complete or len(html_detections) == 0
    if html_unavailable:
        pdf_cache_path = pdf_cache_dir / f"{safe_id}.json"
        if pdf_cache_path.exists():
            try:
                with pdf_cache_path.open("r", encoding="utf-8") as fh:
                    pdf_cached = json.load(fh)
                text = pdf_cached.get("text", "")
                if text:
                    processor = PDFProcessor(input_dir=".", output_dir=".")
                    cleaned_text = processor.clean_text(text)
                    body_text = trim_pdf_text_to_body(cleaned_text)
                    screened_blocks, _ = clean_paper_text_for_language_screening(body_text, _label=paper_id)
                    raw_langs = detect_languages_in_text(screened_blocks, lang_classes, languages_to_ignore, paper_id=paper_id)
                    detections = build_detections(raw_langs, lang_classes, possible_false_positive_languages)
                    pdf_cached.update({
                        "cleaned_text": cleaned_text,
                        "body_text": body_text,
                        "screened_text": "\n\n".join(screened_blocks),
                        "detected_languages": detections,
                    })
                    with pdf_cache_path.open("w", encoding="utf-8") as fh:
                        json.dump(pdf_cached, fh, ensure_ascii=False, indent=2)
                else:
                    detections = pdf_cached.get("detected_languages", [])
                record["sources_checked"].append("pdf")
                if detections:
                    record["sections"]["pdf_full_text"] = {"source": "pdf", "detected_languages": detections}
            except Exception as exc:
                tqdm.write(f"  [{paper_id}] PDF cache reprocess error: {type(exc).__name__}: {exc}")
                record["warnings"].append({"step": "pdf_reprocess", "error": str(exc)})
        else:
            record["warnings"].append({"step": "reprocess", "error": "No HTML or PDF cache found — skipped"})

    tqdm.write(f"  [{paper_id}] reprocessed in {_time.monotonic()-t_paper:.1f}s")
    return record


def reprocess_from_cache(
    papers: list[dict],
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    possible_false_positive_languages: dict[str, str],
    output_jsonl: Path,
    warnings_file: Path,
    html_cache_dir: Path,
    pdf_cache_dir: Path,
    max_workers: int = 4,
) -> dict:
    """Re-run cleaning + detection on cached HTML/PDF text; write new output JSONL."""
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    all_warnings: list[dict] = []
    results: list[dict] = []
    stats = {
        "total_papers": len(papers),
        "papers_with_detections": 0,
        "total_detections": 0,
        "failed_papers": 0,
        "sources": {"abstract": 0, "html": 0, "pdf": 0},
    }

    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {
            executor.submit(
                _reprocess_single_paper,
                paper,
                lang_classes,
                languages_to_ignore,
                possible_false_positive_languages,
                html_cache_dir,
                pdf_cache_dir,
            ): paper
            for paper in papers
        }

        pending = set(futures.keys())
        with tqdm(total=len(futures), desc="Reprocessing papers") as pbar:
            while pending:
                done, pending = wait(pending, timeout=120, return_when=FIRST_COMPLETED)
                if not done:
                    tqdm.write("[reprocess] no paper completed in 120s — possible stall, continuing…")
                    continue
                for future in done:
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
                        tqdm.write(f"  ERROR: [{paper.get('id', 'unknown')}] {type(exc).__name__}: {exc}")
                        all_warnings.append({
                            "paper_id": paper.get("id", "unknown"),
                            "error": str(exc),
                            "timestamp": datetime.now().isoformat(),
                        })
                        stats["failed_papers"] += 1
                    pbar.update(1)
    finally:
        executor.shutdown(wait=False)

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
    print(f"Sources — abstract:{stats['sources']['abstract']}  html:{stats['sources']['html']}  pdf:{stats['sources']['pdf']}")
    print(f"Output: {output_jsonl}")

    return stats


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def process_papers(
    papers: list[dict],
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    possible_false_positive_languages: dict[str, str],
    output_jsonl: Path,
    warnings_file: Path,
    pdf_dir: Path,
    html_cache_dir: Path,
    pdf_cache_dir: Path,
    max_workers: int = 4,
) -> dict:
    for d in [pdf_dir, html_cache_dir, pdf_cache_dir]:
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

    import time as _time
    _PER_PAPER_TIMEOUT = 600  # seconds before a stuck paper is skipped (HTML 120s + PDF 180s + processing headroom)

    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {
            executor.submit(
                _process_single_paper,
                paper,
                lang_classes,
                languages_to_ignore,
                possible_false_positive_languages,
                pdf_dir,
                html_cache_dir,
                pdf_cache_dir,
            ): paper
            for paper in papers
        }

        pending = set(futures.keys())
        tqdm.write(f"[loop] submitted {len(futures)} futures with max_workers={max_workers}")
        with tqdm(total=len(futures), desc="Processing papers") as pbar:
            while pending:
                tqdm.write(f"[loop] waiting on {len(pending)} pending futures…")
                t_wait = _time.monotonic()
                done, pending = wait(pending, timeout=_PER_PAPER_TIMEOUT, return_when=FIRST_COMPLETED)
                elapsed_wait = _time.monotonic() - t_wait
                tqdm.write(f"[loop] wait returned: {len(done)} done, {len(pending)} still pending (waited {elapsed_wait:.1f}s)")

                if not done:
                    # No future completed within timeout — all pending workers are stuck
                    tqdm.write(f"[loop] TIMEOUT — no paper completed in {_PER_PAPER_TIMEOUT}s, stuck papers:")
                    for f in list(pending):
                        stuck_paper = futures[f]
                        pid = stuck_paper.get("id", "unknown")
                        tqdm.write(f"  TIMEOUT: [{pid}] no response after {_PER_PAPER_TIMEOUT}s — skipping")
                        all_warnings.append({
                            "paper_id": pid,
                            "error": f"worker timeout after {_PER_PAPER_TIMEOUT}s",
                            "timestamp": datetime.now().isoformat(),
                        })
                        stats["failed_papers"] += 1
                        pbar.update(1)
                    pending.clear()
                    break

                for future in done:
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
                        tqdm.write(f"  ERROR: [{paper.get('id', 'unknown')}] {type(exc).__name__}: {exc}")
                        all_warnings.append({
                            "paper_id": paper.get("id", "unknown"),
                            "error": str(exc),
                            "timestamp": datetime.now().isoformat(),
                        })
                        stats["failed_papers"] += 1
                    pbar.update(1)
    finally:
        # Don't block on stuck threads — daemon threads will be reaped when the process exits
        executor.shutdown(wait=False)

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
        default=None,
        help="Week output directory (default: auto-derived from input filename as data/processed/weeks/YYYYMMDD_to_YYYYMMDD/)",
    )
    parser.add_argument("--workers", type=int, default=4, help="Parallel worker threads (default: 4)")
    parser.add_argument(
        "--reprocess-cache",
        action="store_true",
        help=(
            "Skip HTML/PDF downloads; re-run text cleaning + language detection on cached "
            "html_cache/*.json and pdf_cache/*.json files and rewrite the output JSONL. "
            "Use after updating text cleaning logic."
        ),
    )
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

    # Derive week output dir from input filename if not given
    import re as _re
    if args.output_dir is not None:
        output_dir = args.output_dir
    else:
        m = _re.search(r'(\d{8}_to_\d{8})', args.input.stem)
        output_dir = _DEFAULT_PROCESSED_DIR / "weeks" / m.group(1) if m else _DEFAULT_PROCESSED_DIR

    lang_classes, languages_to_ignore, possible_false_positive_languages = load_language_data(args.language_data)
    print(f"Loaded {sum(len(v) for v in lang_classes.values())} language entries across {len(lang_classes)} classes")
    print(f"Suspicious languages for review: {len(possible_false_positive_languages)}")

    papers = load_papers(args.input)
    print(f"Loaded {len(papers)} papers from {args.input}")

    stem = args.input.stem
    html_cache_dir = output_dir / "html_cache"
    pdf_cache_dir = output_dir / "pdf_cache"

    if args.reprocess_cache:
        print(f"--reprocess-cache: re-running cleaning+detection on cached extractions in {output_dir}")
        reprocess_from_cache(
            papers=papers,
            lang_classes=lang_classes,
            languages_to_ignore=languages_to_ignore,
            possible_false_positive_languages=possible_false_positive_languages,
            output_jsonl=output_dir / f"{stem}_detected.jsonl",
            warnings_file=output_dir / f"{stem}_warnings.json",
            html_cache_dir=html_cache_dir,
            pdf_cache_dir=pdf_cache_dir,
            max_workers=args.workers,
        )
    else:
        process_papers(
            papers=papers,
            lang_classes=lang_classes,
            languages_to_ignore=languages_to_ignore,
            possible_false_positive_languages=possible_false_positive_languages,
            output_jsonl=output_dir / f"{stem}_detected.jsonl",
            warnings_file=output_dir / f"{stem}_warnings.json",
            pdf_dir=Path(__file__).parent.parent / "data/raw/pdfs",
            html_cache_dir=html_cache_dir,
            pdf_cache_dir=pdf_cache_dir,
            max_workers=args.workers,
        )


if __name__ == "__main__":
    main()
