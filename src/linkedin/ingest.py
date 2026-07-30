"""Mechanical PDF text extraction for the LinkedIn Streamlit uploader - no
reasoning here, same split as profile/ingest.py. Handles both PDF shapes
Zahir generates himself (per PRD §13's manual-export-only constraint):
LinkedIn's own "Save to PDF" export, and a browser print-to-PDF of the
profile page. Both are text-based (not scanned images), so plain extraction
is sufficient - no OCR needed.
"""

from pypdf import PdfReader


def extract_text_from_pdf(file) -> str:
    """file: a path (str/Path) or a file-like object opened in binary mode
    (e.g. Streamlit's UploadedFile, which pypdf can read directly)."""
    reader = PdfReader(file)
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p for p in parts if p.strip())
