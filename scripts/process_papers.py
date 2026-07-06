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
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

# Prevent HuggingFace tokenizers from forking worker processes inside threads,
# which causes multiprocessing semaphore leaks and SIGSEGV on macOS/Py3.13.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

sys.path.insert(0, str(Path(__file__).parent.parent))

from langtrend.manifest import build_detections
from langtrend.text_cleaning import clean_paper_text_for_language_screening, detect_languages_in_text, trim_pdf_text_to_body, extract_paper_acronyms, find_language_acronym_conflicts
from langtrend.html_processor import recheck_languages_from_html, is_removable_heading
from langtrend.pdf_processor import PDFProcessor, download_pdf as _download_pdf

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


_PDF_EXTRACT_RETRIES = 2  # re-download and retry if extraction fails (e.g. truncated file)


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
    no_pdf: bool = False,
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
        html_cache, is_html_complete, acronym_conflicts = recheck_languages_from_html(
            paper,
            lang_classes,
            languages_to_ignore,
            out_dir=html_cache_dir,
        )
        if html_cache is not None:
            tqdm.write(f"  [{paper_id}] HTML ok ({len(html_cache)} sections, complete={is_html_complete}) in {_time.monotonic()-t_html:.1f}s")
            if acronym_conflicts:
                for conflict in acronym_conflicts:
                    record["warnings"].append({
                        "step": "acronym_language_conflict",
                        "acronym": conflict["acronym"],
                        "language": conflict["language"],
                        "language_class": conflict["class"],
                        "message": (
                            f"Paper defines '{conflict['acronym']}' as an acronym. "
                            f"The language '{conflict['language']}' (class {conflict['class']}) "
                            f"shares this name — mentions may have been suppressed. Manual review recommended."
                        ),
                    })
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
    if html_unavailable and no_pdf:
        tqdm.write(f"  [{paper_id}] PDF skipped (--no-pdf)")
    if html_unavailable and not no_pdf:
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
                for extract_attempt in range(1, _PDF_EXTRACT_RETRIES + 1):
                    try:
                        t_extract = _time.monotonic()
                        processor = PDFProcessor(input_dir=str(pdf_path.parent), output_dir=str(pdf_path.parent))
                        raw_text, _ = processor.extract_text(pdf_path)
                        tqdm.write(f"  [{paper_id}] PDF text extracted ({len(raw_text)} chars) in {_time.monotonic()-t_extract:.1f}s")
                        record["sources_checked"].append("pdf")
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
                        break  # extraction succeeded
                    except Exception as exc:
                        tqdm.write(f"  [{paper_id}] PDF extract error (attempt {extract_attempt}/{_PDF_EXTRACT_RETRIES}): {type(exc).__name__}: {exc}")
                        pdf_path.unlink(missing_ok=True)  # remove corrupt file before retry
                        if extract_attempt < _PDF_EXTRACT_RETRIES:
                            tqdm.write(f"  [{paper_id}] Re-downloading PDF for extraction retry…")
                            pdf_path = _download_pdf(pdf_url, pdf_dir, paper_id)
                            if not pdf_path:
                                record["warnings"].append({"step": "pdf_processing", "error": f"Re-download failed after extract error: {exc}"})
                                record["sources_checked"].append("pdf_unavailable")
                                break
                        else:
                            record["warnings"].append({"step": "pdf_processing", "error": str(exc)})
                            record["sources_checked"].append("pdf_unavailable")
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
    html_sections_with_text = 0

    if html_cache_path.exists():
        try:
            with html_cache_path.open("r", encoding="utf-8") as fh:
                html_cached = json.load(fh)
            is_html_complete = html_cached.get("_complete", True)

            updated_cache: dict = {"_complete": is_html_complete}
            if html_cached.get("_unavailable"):
                # Preserve the permanent "no HTML exists for this paper" marker —
                # otherwise a reprocess-cache run would silently drop it, and a
                # later --retry-missing would re-attempt a fetch we already
                # confirmed 404s, instead of skipping it as recheck_languages_from_html intends.
                updated_cache["_unavailable"] = True
            # Build paper-level acronym set from all sections so cross-section uses
            # (e.g. "GAN" defined in Introduction, used in Method) are suppressed.
            paper_acronyms = extract_paper_acronyms("\n\n".join(
                sd.get("text", "") for sd in html_cached.values()
                if isinstance(sd, dict) and not str(sd).startswith("_")
            ))
            acronym_conflicts = find_language_acronym_conflicts(paper_acronyms, lang_classes, languages_to_ignore)
            if acronym_conflicts:
                updated_cache["_acronym_conflicts"] = acronym_conflicts
                for conflict in acronym_conflicts:
                    record["warnings"].append({
                        "step": "acronym_language_conflict",
                        "acronym": conflict["acronym"],
                        "language": conflict["language"],
                        "language_class": conflict["class"],
                        "message": (
                            f"Paper defines '{conflict['acronym']}' as an acronym. "
                            f"The language '{conflict['language']}' (class {conflict['class']}) "
                            f"shares this name — mentions may have been suppressed. Manual review recommended."
                        ),
                    })
            for section_title, section_data in html_cached.items():
                if section_title.startswith("_"):
                    continue
                text = section_data.get("text", "") if isinstance(section_data, dict) else ""
                if not text:
                    updated_cache[section_title] = section_data
                    continue
                html_sections_with_text += 1
                # Skip excluded sections (References, Related Work incl. combined/
                # appendix variants, Acknowledgements, Abstract, …) exactly as a
                # fresh fetch does in recheck_languages_from_html — otherwise a
                # cache reprocess would re-detect languages from their raw text
                # and reintroduce them. Keep the raw text cached; record none.
                if is_removable_heading(section_title):
                    updated_cache[section_title] = {"text": text, "cleaned_text": "", "detected": []}
                    continue
                cleaned_blocks, _ = clean_paper_text_for_language_screening(text, _label=paper_id, paper_acronyms=paper_acronyms)
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
            html_sections_with_text = 0

    # 3. PDF cache — fall back only when HTML is genuinely missing, incomplete, or empty.
    # A complete HTML with no non-English detections is not "unavailable" — it means
    # the paper is English-only and PDF fallback would add no signal.
    html_missing_or_empty = not html_cache_path.exists() or not is_html_complete or html_sections_with_text == 0
    if html_missing_or_empty:
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
        elif not html_cache_path.exists():
            record["warnings"].append({"step": "reprocess", "error": "No HTML or PDF cache found — skipped"})
        elif not is_html_complete:
            record["warnings"].append({"step": "reprocess", "error": "HTML cache incomplete, no PDF cache available — skipped"})

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
    no_detections_file: Path | None = None,
    max_workers: int = 4,
) -> dict:
    """Re-run cleaning + detection on cached HTML/PDF text; write new output JSONL."""
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    all_warnings: list[dict] = []
    no_detection_records: list[dict] = []
    stats = {
        "total_papers": len(papers),
        "papers_with_detections": 0,
        "total_detections": 0,
        "failed_papers": 0,
        "sources": {"abstract": 0, "html": 0, "pdf": 0},
    }

    _fp_out = output_jsonl.open("w", encoding="utf-8")
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
                            pid = record.get("paper_id", "unknown")
                            all_warnings.extend({**w, "paper_id": pid} for w in record["warnings"])
                        if record.get("sections"):
                            _fp_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                            _fp_out.flush()
                            stats["papers_with_detections"] += 1
                            for sec in record["sections"].values():
                                stats["total_detections"] += len(sec.get("detected_languages", []))
                        else:
                            no_detection_records.append({
                                "paper_id": record.get("paper_id"),
                                "title": record.get("paper", {}).get("title"),
                                "sources_checked": record.get("sources_checked", []),
                                "warnings": record.get("warnings", []),
                            })
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
        _fp_out.close()

        if all_warnings:
            with warnings_file.open("w", encoding="utf-8") as fp:
                json.dump(all_warnings, fp, ensure_ascii=False, indent=2)
            print(f"Warnings saved to {warnings_file}")

        _nd_path = no_detections_file or output_jsonl.parent / output_jsonl.name.replace("_detected.jsonl", "_no_detections.json")
        with _nd_path.open("w", encoding="utf-8") as fp:
            json.dump(no_detection_records, fp, ensure_ascii=False, indent=2)
        print(f"No-detection records: {len(no_detection_records)} → {_nd_path}")

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
    no_detections_file: Path | None = None,
    max_workers: int = 4,
    no_pdf: bool = False,
    append_mode: bool = False,
) -> dict:
    for d in [pdf_dir, html_cache_dir, pdf_cache_dir]:
        d.mkdir(parents=True, exist_ok=True)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    all_warnings: list[dict] = []
    no_detection_records: list[dict] = []
    stats = {
        "total_papers": len(papers),
        "papers_with_detections": 0,
        "total_detections": 0,
        "failed_papers": 0,
        "sources": {"abstract": 0, "html": 0, "pdf": 0, "pdf_unavailable": 0},
    }

    import time as _time
    _PER_PAPER_TIMEOUT = 600  # seconds before a stuck paper is skipped (HTML 120s + PDF 180s + processing headroom)

    if not no_pdf:
        from langtrend.pdf_processor import init_docling
        init_docling()

    _fp_out = output_jsonl.open("a" if append_mode else "w", encoding="utf-8")
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
                no_pdf,
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
                            pid = record.get("paper_id", "unknown")
                            all_warnings.extend({**w, "paper_id": pid} for w in record["warnings"])
                        if record.get("sections"):
                            _fp_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                            _fp_out.flush()
                            stats["papers_with_detections"] += 1
                            for sec in record["sections"].values():
                                stats["total_detections"] += len(sec.get("detected_languages", []))
                        else:
                            no_detection_records.append({
                                "paper_id": record.get("paper_id"),
                                "title": record.get("paper", {}).get("title"),
                                "sources_checked": record.get("sources_checked", []),
                                "warnings": record.get("warnings", []),
                            })
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
        _fp_out.close()

        if all_warnings:
            with warnings_file.open("w", encoding="utf-8") as fp:
                json.dump(all_warnings, fp, ensure_ascii=False, indent=2)
            print(f"Warnings saved to {warnings_file}")

        _nd_path = no_detections_file or output_jsonl.parent / output_jsonl.name.replace("_detected.jsonl", "_no_detections.json")
        with _nd_path.open("w", encoding="utf-8") as fp:
            json.dump(no_detection_records, fp, ensure_ascii=False, indent=2)
        print(f"No-detection records: {len(no_detection_records)} → {_nd_path}")

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

def _needs_retry(
    p: dict,
    detected_sources: dict[str, list[str]],
    no_det_sources: dict[str, list[str]],
    cached_html_ids: set[str],
    cached_pdf_ids: set[str],
    html_cache_dir: Path,
) -> bool:
    """Decide whether a paper needs another --retry-missing pass.

    See the checks below: never processed, crash leftovers (cache exists but
    the detection entry never recorded it), incomplete HTML that a raw PDF
    might do better on, and HTML that was never cached at all (a transient
    failure — 429/5xx/timeout/connection error — deliberately leaves no cache
    file, see fetch_arxiv_html) even though PDF already succeeded.
    """
    pid = p["id"]
    safe_id = pid.split("/")[-1]

    sources = detected_sources.get(pid)
    if sources is None:
        nd_sources = no_det_sources.get(pid)
        if nd_sources is None:
            return True  # never processed at all
        # Paper confirmed no detections — skip if html actually succeeded
        # (found nothing, trust it) or pdf succeeded AND html got a real
        # cached attempt (confirmed 404, or a partial download not worth
        # retrying again). Same gap as the detected-paper branch below: pdf
        # succeeding alone doesn't mean html was ever actually attempted —
        # a transient failure leaves no cache file at all (fetch_arxiv_html).
        if "html" in nd_sources:
            return False
        if "pdf" in nd_sources:
            return safe_id not in cached_html_ids
        return True  # abstract-only → worth retrying with html/pdf

    # Paper has detections — check for crash leftovers / incomplete sources.
    # PDF cache exists but detection entry never recorded pdf/html — crashed mid-run
    if safe_id in cached_pdf_ids and "pdf" not in sources and "html" not in sources:
        return True
    # No cache at all AND only abstract-only detection — HTML/PDF was never
    # successfully attempted. Skip this check if html/pdf is already in sources:
    # the cache may simply be absent (e.g. gitignored on a fresh checkout) even
    # though the paper was already fully processed.
    if safe_id not in cached_html_ids and safe_id not in cached_pdf_ids:
        if "html" not in sources and "pdf" not in sources:
            return True
    # HTML cache exists but is incomplete (_complete=False) — a stalled/partial
    # download that's worth retrying, UNLESS it's the permanent "_unavailable"
    # (confirmed 404) sentinel, which retrying would just waste a request on.
    if safe_id in cached_html_ids and "html" not in sources:
        try:
            cached = json.loads((html_cache_dir / f"{safe_id}.json").read_text(encoding="utf-8"))
            if not cached.get("_complete", True) and not cached.get("_unavailable"):
                return True
        except Exception:
            pass
        return False
    # No HTML cache file at all, but PDF already succeeded — the HTML fetch
    # never got far enough to write anything (a transient failure before or
    # during the download, see fetch_arxiv_html), so it's never been given a
    # real shot. Worth retrying since HTML is the richer source when it works.
    if safe_id not in cached_html_ids and "html" not in sources and "pdf" in sources:
        return True
    return False


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
    parser.add_argument(
        "--retry-missing",
        action="store_true",
        help=(
            "Retry papers not yet detected or with no html/pdf cache. Uses cached "
            "extractions where available; downloads only what is still missing. "
            "Merges results into existing detected, warnings, and no-detections files."
        ),
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help=(
            "Skip PDF fallback entirely (no docling, no downloading). Safe to run in "
            "multiple terminals simultaneously. Follow up with --retry-missing to pick "
            "up the ~10%% of papers that need PDF."
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
    elif args.retry_missing:
        detected_path = output_dir / f"{stem}_detected.jsonl"
        warnings_path = output_dir / f"{stem}_warnings.json"
        no_det_path   = output_dir / f"{stem}_no_detections.json"

        if args.retry_missing:
            cached_html_ids = {p.stem for p in html_cache_dir.glob("*.json")}
            cached_pdf_ids  = {p.stem for p in pdf_cache_dir.glob("*.json")}

            # Build maps of paper_id → sources_checked for already-processed papers.
            # detected_sources covers papers WITH language detections (in _detected.jsonl).
            # no_det_sources covers papers confirmed to have NO detections (in _no_detections.json).
            # Together they let us skip papers already fully processed and only retry those
            # that genuinely need another pass (e.g. abstract-only → try HTML/PDF).
            detected_sources: dict[str, list[str]] = {}
            if detected_path.exists():
                for line in detected_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rec = json.loads(line)
                        detected_sources[rec.get("paper_id", "")] = rec.get("sources_checked", [])

            no_det_sources: dict[str, list[str]] = {}
            if no_det_path.exists():
                try:
                    for rec in json.loads(no_det_path.read_text(encoding="utf-8")):
                        no_det_sources[rec.get("paper_id", "")] = rec.get("sources_checked", [])
                except (json.JSONDecodeError, KeyError):
                    pass

            subset = [
                p for p in papers
                if _needs_retry(p, detected_sources, no_det_sources, cached_html_ids, cached_pdf_ids, html_cache_dir)
            ]
            label = f"--retry-missing: {len(subset)} paper(s) not yet detected or missing html/pdf cache"

        if not subset:
            print(f"{label.split(':')[0]}: nothing to do.")
            sys.exit(0)
        print(label)

        # Load existing detected records so we can track what changes after the run.
        existing_lines: list[str] = []
        existing_by_id: dict[str, int] = {}  # paper_id → line index
        if detected_path.exists():
            for i, line in enumerate(detected_path.read_text(encoding="utf-8").splitlines()):
                if line.strip():
                    existing_lines.append(line)
                    existing_by_id[json.loads(line).get("paper_id", "")] = i
        existing_count = len(existing_lines)

        # Re-initialize detected_path with existing records (clean deduplication),
        # then process_papers() will append new records directly as each paper completes.
        with detected_path.open("w", encoding="utf-8") as fp:
            for line in existing_lines:
                fp.write(line + "\n")

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_warnings = Path(tmp.name)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_no_det = Path(tmp.name)

        process_papers(
            papers=subset,
            lang_classes=lang_classes,
            languages_to_ignore=languages_to_ignore,
            possible_false_positive_languages=possible_false_positive_languages,
            output_jsonl=detected_path,
            warnings_file=tmp_warnings,
            pdf_dir=Path(__file__).parent.parent / "data/raw/pdfs",
            html_cache_dir=html_cache_dir,
            pdf_cache_dir=pdf_cache_dir,
            no_detections_file=tmp_no_det,
            max_workers=args.workers,
            no_pdf=args.no_pdf,
            append_mode=True,
        )

        # Deduplicate detected_path: new records were appended after existing ones, so the
        # last occurrence of each paper_id is always the freshest (upgrade case handled automatically).
        all_lines = [l for l in detected_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        last_index: dict[str, int] = {}
        for i, line in enumerate(all_lines):
            last_index[json.loads(line).get("paper_id", "")] = i
        with detected_path.open("w", encoding="utf-8") as fp:
            for i in sorted(last_index.values()):
                fp.write(all_lines[i] + "\n")
        appended = sum(1 for pid in last_index if pid not in existing_by_id)
        replaced = sum(1 for pid, idx in last_index.items() if pid in existing_by_id and idx >= existing_count)
        print(f"Merged {appended} new + {replaced} upgraded detection record(s) into {detected_path}")

        # Merge warnings
        new_warnings = json.loads(tmp_warnings.read_text(encoding="utf-8")) if tmp_warnings.stat().st_size > 2 else []
        if new_warnings:
            existing_warnings = json.loads(warnings_path.read_text(encoding="utf-8")) if warnings_path.exists() else []
            with warnings_path.open("w", encoding="utf-8") as fp:
                json.dump(existing_warnings + new_warnings, fp, ensure_ascii=False, indent=2)
            print(f"Appended {len(new_warnings)} warning(s) to {warnings_path}")

        # Merge no-detections
        new_no_det = json.loads(tmp_no_det.read_text(encoding="utf-8")) if tmp_no_det.stat().st_size > 2 else []
        existing_no_det = json.loads(no_det_path.read_text(encoding="utf-8")) if no_det_path.exists() else []
        # Build index of new no-det records so we can upgrade stale ones (e.g. abstract-only → abstract+pdf)
        new_nd_by_id = {r.get("paper_id"): r for r in new_no_det}
        # Papers now in detected must be removed from no-detections
        now_detected_ids = set(last_index.keys())
        merged_no_det = []
        for r in existing_no_det:
            pid = r.get("paper_id")
            if pid in now_detected_ids:
                continue  # promoted to detected — drop from no-detections
            if pid in new_nd_by_id:
                merged_no_det.append(new_nd_by_id[pid])  # upgrade (e.g. sources_checked updated)
            else:
                merged_no_det.append(r)
        # Append genuinely new no-det records not seen before
        existing_nd_ids = {r.get("paper_id") for r in existing_no_det}
        merged_no_det += [r for r in new_no_det if r.get("paper_id") not in existing_nd_ids and r.get("paper_id") not in now_detected_ids]
        with no_det_path.open("w", encoding="utf-8") as fp:
            json.dump(merged_no_det, fp, ensure_ascii=False, indent=2)
        print(f"No-detections file updated: {len(merged_no_det)} total record(s) → {no_det_path}")

        for tmp_path in (tmp_warnings, tmp_no_det):
            tmp_path.unlink(missing_ok=True)
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
            no_detections_file=output_dir / f"{stem}_no_detections.json",
            max_workers=args.workers,
            no_pdf=args.no_pdf,
        )


if __name__ == "__main__":
    main()
    # Bypass Python's interpreter-shutdown destructor chain.  docling holds a
    # DocumentConverter singleton that owns PyTorch C++ thread pools; their
    # C++ destructors reliably SIGSEGV (-11) when the Python runtime is already
    # partially torn down.  All output files are written per-paper before this
    # point, so hard-exiting is safe.  stdout/stderr are fully buffered (not
    # line-buffered) whenever they're not a TTY — e.g. piped through
    # run_logged.sh's `tee` — so os._exit would otherwise silently drop the
    # summary printed at the end of main().
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
