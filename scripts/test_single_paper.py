#!/usr/bin/env python3
"""
Run a single paper through the LangTrend detection pipeline.

For trying the pipeline on your own paper without fetching a whole week.
Two input modes:
  --arxiv-id    fetches just that paper's metadata, then runs the same
                abstract → HTML → PDF detection cascade process_papers.py uses.
  --pdf-path    skips arXiv entirely and runs the PDF-only detection path on a
                file already on disk (e.g. a paper not on arXiv, or one you
                already downloaded) — no abstract or HTML to scan, so the PDF
                is always used.

Either way, a report is printed and caches (HTML/PDF text, detections) are
written under data/sandbox/<paper-id>/ so this never touches the real weekly
data.

Usage:
    python scripts/test_single_paper.py --arxiv-id 2606.16047
    python scripts/test_single_paper.py --arxiv-id https://arxiv.org/abs/2606.16047v1
    python scripts/test_single_paper.py --arxiv-id 2606.16047 --no-pdf
    python scripts/test_single_paper.py --arxiv-id 2606.16047 --judge
    python scripts/test_single_paper.py --pdf-path ~/Downloads/some_paper.pdf
    python scripts/test_single_paper.py --pdf-path ~/Downloads/some_paper.pdf --title "Some Paper" --judge
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Prevent HuggingFace tokenizers from forking worker processes inside threads,
# which causes multiprocessing semaphore leaks and SIGSEGV on macOS/Py3.13.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import arxiv

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from process_papers import _process_single_paper, load_language_data, _DEFAULT_LANG_DATA  # noqa: E402

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_SANDBOX_DIR = _PROJECT_ROOT / "data/sandbox"
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5}(v\d+)?)")


def normalize_arxiv_id(raw: str) -> str:
    """Accept a bare id, versioned id, or full arxiv.org URL; return e.g. '2606.16047v1'."""
    match = _ARXIV_ID_RE.search(raw)
    if not match:
        raise ValueError(f"Could not find an arXiv id in '{raw}'")
    return match.group(1)


def fetch_paper_metadata(arxiv_id: str) -> dict:
    client = arxiv.Client()
    result = next(client.results(arxiv.Search(id_list=[arxiv_id])), None)
    if result is None:
        raise ValueError(f"arXiv has no paper with id '{arxiv_id}'")
    return {
        "id": result.entry_id,
        "title": result.title,
        "abstract": result.summary,
        "authors": [author.name for author in result.authors],
        "published": result.published.isoformat(),
        "updated": result.updated.isoformat(),
        "categories": list(result.categories),
        "pdf_url": result.pdf_url,
        "_fetch_source": "arxiv_api",
    }


def slugify_filename(name: str) -> str:
    """A PDF's filename stem, sanitized into a safe paper-id-like slug."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return slug or "local-pdf"


def process_local_pdf(
    pdf_path: Path,
    paper_id: str,
    title: str,
    lang_classes: dict,
    languages_to_ignore: set,
    possible_false_positive_languages: dict,
    pdf_cache_dir: Path,
) -> dict:
    """Run the PDF-only detection path on a paper already sitting on disk.

    Mirrors the PDF branch of process_papers._process_single_paper, minus the
    download step (the file is already local) and the abstract/HTML steps
    (there's no arXiv metadata to scan). Writes the same pdf_cache/<id>.json
    shape so judge.py's context assembly works on the result unchanged.
    """
    from langtrend.manifest import build_detections
    from langtrend.pdf_processor import PDFProcessor
    from langtrend.text_cleaning import (
        clean_paper_text_for_language_screening,
        detect_languages_in_text,
        trim_pdf_text_to_body,
    )

    record: dict = {
        "paper_id": paper_id,
        "paper": {"id": paper_id, "title": title, "abstract": "", "pdf_url": str(pdf_path)},
        "sources_checked": [],
        "sections": {},
        "warnings": [],
    }

    processor = PDFProcessor(input_dir=str(pdf_path.parent), output_dir=str(pdf_path.parent))
    raw_text, _ = processor.extract_text(pdf_path)
    record["sources_checked"].append("pdf")
    if not raw_text:
        record["warnings"].append({"step": "pdf_processing", "error": "No text could be extracted from the PDF"})
        return record

    cleaned_text = processor.clean_text(raw_text)
    body_text = trim_pdf_text_to_body(cleaned_text)
    screened_blocks, _ = clean_paper_text_for_language_screening(body_text, _label=paper_id)
    raw_langs = detect_languages_in_text(screened_blocks, lang_classes, languages_to_ignore, paper_id=paper_id)
    detections = build_detections(raw_langs, lang_classes, possible_false_positive_languages)
    if detections:
        record["sections"]["pdf_full_text"] = {"source": "pdf", "detected_languages": detections}

    pdf_cache_dir.mkdir(parents=True, exist_ok=True)
    with (pdf_cache_dir / f"{paper_id}.json").open("w", encoding="utf-8") as fh:
        json.dump({
            "paper_id": paper_id,
            "text": raw_text,
            "cleaned_text": cleaned_text,
            "body_text": body_text,
            "screened_text": "\n\n".join(screened_blocks),
            "detected_languages": detections,
        }, fh, ensure_ascii=False, indent=2)

    return record


def print_report(record: dict) -> None:
    paper = record.get("paper", {})
    print(f"\nTitle: {paper.get('title', '')}")
    print(f"Paper id: {record.get('paper_id')}")
    print(f"Sources checked: {', '.join(record.get('sources_checked', [])) or '(none)'}")

    sections = record.get("sections", {})
    if not sections:
        print("\nNo languages detected (paper looks English-only, or nothing matched).")
    else:
        print("\nDetected languages by section:")
        for name, data in sections.items():
            langs = ", ".join(d["language"] for d in data["detected_languages"])
            print(f"  [{data['source']:<11}] {name}: {langs}")

    if record.get("warnings"):
        print("\nWarnings:")
        for w in record["warnings"]:
            print(f"  - {w}")


def run_judge(record: dict, week_dir: Path, model: str | None, base_url: str | None, save: bool) -> None:
    from langtrend.judge import judge_paper, save_judge_record
    from langtrend.llm_client import LLMClientConfig, OpenAICompatClient

    config = LLMClientConfig.from_env()
    if model:
        config.model = model
    if base_url:
        config.base_url = base_url.rstrip("/")
    if not config.api_key and not config.is_local():
        print(
            "\n--judge: LLM_JUDGE_API_KEY is not set (required for hosted endpoints).\n"
            "Set it in .env (see .env.example), or point --base-url at a local server (e.g. Ollama).",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\nJudging with {config.model} …")
    client = OpenAICompatClient(config)
    client.ping()
    judge_record = judge_paper(record, week_dir, client, config)
    if judge_record is None:
        print("  (no languages need judging)")
        return

    verdicts = judge_record.get("verdicts", {})
    print("\nVerdicts:")
    if not verdicts:
        print("  (no verdicts returned)")
    else:
        width = max(len(name) for name in verdicts)
        for name, v in sorted(verdicts.items(), key=lambda kv: kv[1]["verdict"]):
            print(f"  {name:<{width}}  {v['verdict']:<15} {v['reason']}")

    if save:
        path = save_judge_record(week_dir, judge_record)
        print(f"\nSaved: {path}")
    else:
        print("\n(not saved — pass --save to write the judge cache)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a single paper through the LangTrend detection pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--arxiv-id", help="arXiv id, versioned id, or full arxiv.org URL")
    source.add_argument("--pdf-path", type=Path, help="Path to a PDF already on disk (skips arXiv, PDF-only detection)")
    parser.add_argument("--title", type=str, default=None, help="Paper title (only used with --pdf-path; default: filename)")
    parser.add_argument("--paper-id", type=str, default=None, help="Custom id for the sandbox dir/report (only used with --pdf-path; default: sanitized filename)")
    parser.add_argument("--language-data", type=Path, default=_DEFAULT_LANG_DATA)
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Sandbox directory for caches and results (default: data/sandbox/<paper-id>)",
    )
    parser.add_argument("--no-pdf", action="store_true", help="Skip the PDF fallback (HTML/abstract only; --arxiv-id only)")
    parser.add_argument("--judge", action="store_true", help="Also run the LLM judge stage on the detections")
    parser.add_argument("--save", action="store_true", help="Save judge verdicts to the sandbox judge_cache (only with --judge)")
    parser.add_argument("--model", type=str, default=None, help="Override LLM_JUDGE_MODEL (only with --judge)")
    parser.add_argument("--base-url", type=str, default=None, help="Override LLM_JUDGE_BASE_URL (only with --judge)")
    args = parser.parse_args()

    if args.pdf_path and args.no_pdf:
        print("Error: --no-pdf has no effect with --pdf-path (that mode is PDF-only detection).", file=sys.stderr)
        sys.exit(1)

    if not args.language_data.exists():
        print(
            f"Error: language data file not found: {args.language_data}\n"
            "Run scripts/extract_language_data.py first (needs the submodule: "
            "git submodule update --init --recursive).",
            file=sys.stderr,
        )
        sys.exit(1)

    lang_classes, languages_to_ignore, possible_false_positive_languages = load_language_data(args.language_data)

    if args.pdf_path:
        pdf_path = args.pdf_path.expanduser().resolve()
        if not pdf_path.exists():
            print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
            sys.exit(1)

        safe_id = args.paper_id or slugify_filename(pdf_path.stem)
        title = args.title or pdf_path.stem

        output_dir = args.output_dir or (_DEFAULT_SANDBOX_DIR / safe_id)
        pdf_cache_dir = output_dir / "pdf_cache"

        print(f"Extracting and detecting languages in {pdf_path.name} …")
        record = process_local_pdf(
            pdf_path, safe_id, title,
            lang_classes, languages_to_ignore, possible_false_positive_languages,
            pdf_cache_dir,
        )
    else:
        arxiv_id = normalize_arxiv_id(args.arxiv_id)
        print(f"Fetching metadata for arXiv:{arxiv_id} …")
        paper = fetch_paper_metadata(arxiv_id)
        safe_id = paper["id"].split("/")[-1]

        output_dir = args.output_dir or (_DEFAULT_SANDBOX_DIR / safe_id)
        html_cache_dir = output_dir / "html_cache"
        pdf_cache_dir = output_dir / "pdf_cache"
        pdf_dir = _PROJECT_ROOT / "data/raw/pdfs"
        for d in (html_cache_dir, pdf_cache_dir, pdf_dir):
            d.mkdir(parents=True, exist_ok=True)

        if not args.no_pdf:
            from langtrend.pdf_processor import init_docling
            init_docling()

        print("Running detection cascade (abstract → HTML → PDF) …")
        record = _process_single_paper(
            paper,
            lang_classes,
            languages_to_ignore,
            possible_false_positive_languages,
            pdf_dir,
            html_cache_dir,
            pdf_cache_dir,
            no_pdf=args.no_pdf,
        )

    print_report(record)

    result_path = output_dir / f"{safe_id}_detected.json"
    with result_path.open("w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    print(f"\nFull record saved to: {result_path}")
    print(f"Cached HTML/PDF text:  {output_dir}")

    if args.judge:
        run_judge(record, output_dir, args.model, args.base_url, args.save)


if __name__ == "__main__":
    main()
    # Bypass Python's interpreter-shutdown destructor chain, same as
    # process_papers.py — docling's DocumentConverter singleton owns PyTorch
    # C++ thread pools whose destructors reliably SIGSEGV on partial teardown.
    # Flush first: stdout/stderr are fully buffered whenever not a TTY (e.g.
    # piped to a file), and os._exit would otherwise drop the report above.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
