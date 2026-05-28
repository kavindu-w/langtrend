#!/usr/bin/env python3

"""
PDF Processor for Academic Research

Extracts text and metadata from academic PDFs using docling for layout-aware
parsing (correct column ordering, clean section separation).

Usage:
    python pdf_processor.py --input <pdf_directory> --output <output_directory>
"""

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import json


def _get_docling_converter():
    """Lazy-initialise a shared docling DocumentConverter (CPU-only).

    Docling's RT-DETR layout model uses float64 which Apple MPS doesn't
    support, so we force CPU.  The converter is expensive to build (model
    weights load once) so we create it on first call and reuse it.
    """
    import torch
    # Must be set before docling imports torch internals.
    torch.set_default_device("cpu")

    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        AcceleratorOptions,
        AcceleratorDevice,
    )
    from docling.datamodel.base_models import InputFormat

    opts = PdfPipelineOptions()
    opts.do_ocr = False             # arXiv PDFs are born-digital, no OCR needed
    opts.do_table_structure = False  # table cells not needed for language detection
    opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


_DOCLING_CONVERTER: Optional[object] = None  # module-level singleton
_DOCLING_LOCK: object = None  # threading.Lock, initialised lazily to avoid import at module load


# Markdown heading prefix (docling output) → strip for plain text
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
# References / bibliography heading in docling markdown output
_MD_REFS_RE = re.compile(
    r"^#{1,6}\s+(?:References|Bibliography|Related [Ww]ork|Acknowledgements?|Acknowledgments?|Funding|Ethics(?: Statement)?)\s*$",
    re.MULTILINE,
)


class PDFProcessor:
    """Extract text and metadata from academic PDFs."""

    def __init__(self, input_dir: str, output_dir: str):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.stats = {"processed": 0, "failed": 0, "total": 0}

    def extract_metadata(self, pdf_path: Path) -> Dict:
        """Extract metadata from PDF using pypdf."""
        from pypdf import PdfReader

        metadata = {
            "filename": pdf_path.name,
            "title": None,
            "author": None,
            "subject": None,
            "creator": None,
            "producer": None,
            "creation_date": None,
            "page_count": None,
        }
        try:
            reader = PdfReader(str(pdf_path))
            info = reader.metadata
            if info:
                metadata["title"] = info.get("/Title")
                metadata["author"] = info.get("/Author")
                metadata["subject"] = info.get("/Subject")
                metadata["creator"] = info.get("/Creator")
                metadata["producer"] = info.get("/Producer")
                creation_date = info.get("/CreationDate")
                if creation_date:
                    metadata["creation_date"] = str(creation_date)
            metadata["page_count"] = len(reader.pages)
        except Exception as e:
            print(f"  Warning: Could not extract metadata: {e}")
        return metadata

    def extract_text(self, pdf_path: Path) -> tuple[str, Dict]:
        """Extract text from PDF using docling (layout-aware, column-correct).

        Returns (plain_text, {}).  The plain text has end-matter sections
        (References, Acknowledgements, etc.) already stripped — no need to
        call trim_pdf_text_to_body separately, though it is harmless to do so.

        Falls back to pdfplumber if docling fails.
        """
        import threading
        global _DOCLING_CONVERTER, _DOCLING_LOCK
        if _DOCLING_LOCK is None:
            _DOCLING_LOCK = threading.Lock()

        with _DOCLING_LOCK:
            if _DOCLING_CONVERTER is None:
                print(f"    [docling] loading models (first call)…", flush=True)
                _DOCLING_CONVERTER = _get_docling_converter()

        try:
            with _DOCLING_LOCK:
                print(f"    [docling] converting {pdf_path.name}…", flush=True)
                result = _DOCLING_CONVERTER.convert(pdf_path)
            md = result.document.export_to_markdown()

            # Cut at the first end-matter heading (References, Acknowledgements …)
            m = _MD_REFS_RE.search(md)
            body_md = md[: m.start()] if m else md

            # Strip markdown heading markers to get plain text
            text = _MD_HEADING_RE.sub("", body_md)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()

            print(f"    [docling] extracted {len(text)} chars from {pdf_path.name}", flush=True)
            return text, {}

        except Exception as e:
            print(f"    [docling] failed ({type(e).__name__}: {e}), falling back to pdfplumber", flush=True)
            return self._extract_text_pdfplumber(pdf_path)

    def _extract_text_pdfplumber(self, pdf_path: Path) -> tuple[str, Dict]:
        """Fallback extraction using pdfplumber (no column awareness)."""
        import pdfplumber

        full_text: list[str] = []
        page_texts: Dict = {}
        try:
            with pdfplumber.open(pdf_path) as pdf:
                total = len(pdf.pages)
                print(f"    [pdfplumber] {total} pages in {pdf_path.name}", flush=True)
                for page_num, page in enumerate(pdf.pages, 1):
                    try:
                        text = page.extract_text()
                        if text:
                            full_text.append(text)
                            page_texts[page_num] = text
                    except Exception as e:
                        print(f"  Warning: Error extracting page {page_num}: {e}")
        except Exception as e:
            raise Exception(f"Failed to open PDF: {e}")
        return "\n\n".join(full_text), page_texts

    def clean_text(self, text: str) -> str:
        """Basic text cleaning (applied after extract_text)."""
        # Rejoin hyphenated line breaks: "dura-\ntion" → "duration"
        text = re.sub(r"([a-z])-\n([a-z])", r"\1\2", text)
        # PDF/docling artifact: space before hyphen with optional newline: "anecdo -tal" → "anecdotal"
        text = re.sub(r"([a-z]) -\n?([a-z])", r"\1\2", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        text = text.replace("\x0c", "")
        return text.strip()

    def generate_markdown(self, pdf_path: Path, metadata: Dict, text: str) -> str:
        """Generate markdown output with metadata and extracted text."""
        md_parts = [f"# {metadata.get('title') or pdf_path.stem}\n", "## Document Metadata\n"]
        md_parts.append(f"- **Filename:** {metadata['filename']}")
        if metadata.get("author"):
            md_parts.append(f"- **Author:** {metadata['author']}")
        if metadata.get("subject"):
            md_parts.append(f"- **Subject:** {metadata['subject']}")
        if metadata.get("creation_date"):
            md_parts.append(f"- **Date:** {metadata['creation_date']}")
        if metadata.get("page_count"):
            md_parts.append(f"- **Pages:** {metadata['page_count']}")
        md_parts.append(f"- **Processed:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        md_parts.append("## Extracted Text\n")
        md_parts.append(text)
        return "\n".join(md_parts)

    def process_pdf(self, pdf_path: Path) -> bool:
        """Process a single PDF file."""
        print(f"\nProcessing: {pdf_path.name}")
        try:
            metadata = self.extract_metadata(pdf_path)
            full_text, _ = self.extract_text(pdf_path)
            if not full_text:
                print("  Warning: No text extracted from PDF")
                return False
            full_text = self.clean_text(full_text)
            markdown = self.generate_markdown(pdf_path, metadata, full_text)
            output_path = self.output_dir / (pdf_path.stem + ".md")
            output_path.write_text(markdown, encoding="utf-8")
            json_path = self.output_dir / (pdf_path.stem + "_metadata.json")
            json_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            print(f"  OK: {len(full_text)} chars → {output_path}")
            return True
        except Exception as e:
            print(f"  Failed: {e}")
            return False

    def process_directory(self) -> Dict:
        """Process all PDFs in the input directory."""
        pdf_files = list(self.input_dir.glob("*.pdf"))
        self.stats["total"] = len(pdf_files)
        if not pdf_files:
            print("No PDF files found.")
            return self.stats
        for pdf_path in pdf_files:
            if self.process_pdf(pdf_path):
                self.stats["processed"] += 1
            else:
                self.stats["failed"] += 1
        return self.stats


def main():
    parser = argparse.ArgumentParser(description="Extract text from academic PDFs")
    parser.add_argument("--input", "-i", required=True, help="Directory containing PDFs")
    parser.add_argument("--output", "-o", required=True, help="Directory for output files")
    args = parser.parse_args()
    processor = PDFProcessor(args.input, args.output)
    stats = processor.process_directory()
    exit(0 if stats["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
