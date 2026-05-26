#!/usr/bin/env python3
# Taken from:
# https://github.com/michalvalco/academic-research-toolkit
# MIT License

"""
PDF Processor for Academic Research

Extracts text and metadata from academic PDFs, outputting structured markdown files.
This is the foundational component that other tools will build upon.

Usage:
    python pdf_processor.py --input <pdf_directory> --output <output_directory>
"""

import argparse
import pdfplumber
from pypdf import PdfReader
from pathlib import Path
import json
from datetime import datetime
from typing import Dict, List, Optional
import re


class PDFProcessor:
    """Extract text and metadata from academic PDFs."""
    
    def __init__(self, input_dir: str, output_dir: str):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Statistics
        self.stats = {
            'processed': 0,
            'failed': 0,
            'total': 0
        }
    
    def extract_metadata(self, pdf_path: Path) -> Dict:
        """Extract metadata from PDF using pypdf."""
        metadata = {
            'filename': pdf_path.name,
            'title': None,
            'author': None,
            'subject': None,
            'creator': None,
            'producer': None,
            'creation_date': None,
            'page_count': None
        }
        
        try:
            reader = PdfReader(str(pdf_path))
            info = reader.metadata
            
            if info:
                metadata['title'] = info.get('/Title', None)
                metadata['author'] = info.get('/Author', None)
                metadata['subject'] = info.get('/Subject', None)
                metadata['creator'] = info.get('/Creator', None)
                metadata['producer'] = info.get('/Producer', None)
                
                # Handle creation date
                creation_date = info.get('/CreationDate', None)
                if creation_date:
                    metadata['creation_date'] = str(creation_date)
            
            metadata['page_count'] = len(reader.pages)
            
        except Exception as e:
            print(f"  Warning: Could not extract metadata: {e}")
        
        return metadata
    
    def extract_text(self, pdf_path: Path) -> tuple[str, Dict]:
        """Extract text from PDF using pdfplumber."""
        full_text = []
        page_texts = {}

        try:
            with pdfplumber.open(pdf_path) as pdf:
                total = len(pdf.pages)
                print(f"    pdfplumber: {total} pages in {pdf_path.name}")
                for page_num, page in enumerate(pdf.pages, 1):
                    print(f"    extracting page {page_num}/{total}…", flush=True)
                    try:
                        text = page.extract_text()
                        if text:
                            full_text.append(text)
                            page_texts[page_num] = text
                    except Exception as e:
                        print(f"  Warning: Error extracting page {page_num}: {e}")
                        continue

        except Exception as e:
            raise Exception(f"Failed to open PDF: {e}")

        return '\n\n'.join(full_text), page_texts
    
    def clean_text(self, text: str) -> str:
        """Basic text cleaning."""
        # Join hyphenated line breaks (PDF line-break artifact: "dura-\ntion" → "duration").
        # Only lowercase-to-lowercase to avoid merging intentional breaks like "State-\nCapital".
        text = re.sub(r'([a-z])-\n([a-z])', r'\1\2', text)

        # Remove excessive whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)

        # Remove form feed characters
        text = text.replace('\x0c', '')

        return text.strip()
    
    def generate_markdown(self, pdf_path: Path, metadata: Dict, text: str) -> str:
        """Generate markdown output with metadata and extracted text."""
        
        md_parts = []
        
        # Header
        md_parts.append(f"# {metadata.get('title') or pdf_path.stem}\n")
        
        # Metadata section
        md_parts.append("## Document Metadata\n")
        md_parts.append(f"- **Filename:** {metadata['filename']}")
        
        if metadata.get('author'):
            md_parts.append(f"- **Author:** {metadata['author']}")
        
        if metadata.get('subject'):
            md_parts.append(f"- **Subject:** {metadata['subject']}")
        
        if metadata.get('creation_date'):
            md_parts.append(f"- **Date:** {metadata['creation_date']}")
        
        if metadata.get('page_count'):
            md_parts.append(f"- **Pages:** {metadata['page_count']}")
        
        md_parts.append(f"- **Processed:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Extracted text
        md_parts.append("## Extracted Text\n")
        md_parts.append(text)
        
        return '\n'.join(md_parts)
    
    def process_pdf(self, pdf_path: Path) -> bool:
        """Process a single PDF file."""
        print(f"\nProcessing: {pdf_path.name}")
        
        try:
            # Extract metadata
            print("  Extracting metadata...")
            metadata = self.extract_metadata(pdf_path)
            
            # Extract text
            print("  Extracting text...")
            full_text, page_texts = self.extract_text(pdf_path)
            
            if not full_text:
                print("  ⚠️  Warning: No text extracted from PDF")
                return False
            
            # Clean text
            full_text = self.clean_text(full_text)
            
            # Generate markdown
            print("  Generating markdown...")
            markdown = self.generate_markdown(pdf_path, metadata, full_text)
            
            # Save output
            output_filename = pdf_path.stem + '.md'
            output_path = self.output_dir / output_filename
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown)
            
            # Also save metadata as JSON for programmatic access
            json_filename = pdf_path.stem + '_metadata.json'
            json_path = self.output_dir / json_filename
            
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
            
            print(f"  ✓ Success! Extracted {len(full_text)} characters")
            print(f"    Saved to: {output_path}")
            
            return True
            
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            return False
    
    def process_directory(self) -> Dict:
        """Process all PDFs in the input directory."""
        
        print(f"\n{'='*60}")
        print(f"PDF Processor - Academic Research Tools")
        print(f"{'='*60}")
        print(f"\nInput directory:  {self.input_dir}")
        print(f"Output directory: {self.output_dir}\n")
        
        # Find all PDFs
        pdf_files = list(self.input_dir.glob('*.pdf'))
        self.stats['total'] = len(pdf_files)
        
        if not pdf_files:
            print("No PDF files found in input directory.")
            return self.stats
        
        print(f"Found {len(pdf_files)} PDF file(s)\n")
        print(f"{'-'*60}")
        
        # Process each PDF
        for pdf_path in pdf_files:
            success = self.process_pdf(pdf_path)
            
            if success:
                self.stats['processed'] += 1
            else:
                self.stats['failed'] += 1
        
        # Summary
        print(f"\n{'-'*60}")
        print(f"\n📊 Processing Summary:")
        print(f"  Total files:     {self.stats['total']}")
        print(f"  ✓ Processed:     {self.stats['processed']}")
        print(f"  ✗ Failed:        {self.stats['failed']}")
        print(f"\n{'='*60}\n")
        
        return self.stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Extract text and metadata from academic PDFs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pdf_processor.py --input ./papers --output ./extracted
  python pdf_processor.py -i ~/research/pdfs -o ~/research/markdown
        """
    )
    
    parser.add_argument(
        '--input', '-i',
        required=True,
        help='Directory containing PDF files'
    )
    
    parser.add_argument(
        '--output', '-o',
        required=True,
        help='Directory for output markdown files'
    )
    
    args = parser.parse_args()
    
    # Create processor and run
    processor = PDFProcessor(args.input, args.output)
    stats = processor.process_directory()
    
    # Exit with appropriate code
    exit(0 if stats['failed'] == 0 else 1)


if __name__ == '__main__':
    main()