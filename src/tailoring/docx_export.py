"""Converts a drafted document's plain text into a real .docx file Zahir
can attach to an actual application - most application portals require an
uploaded file, not pasted text (Zahir's explicit request 2026-07-30: "these
need to be attached"). Generated fresh in memory each time a download is
requested, never written to disk, so the underlying drafted text can stay
encrypted at rest in applications.json like everything else in data/
without this needing its own plaintext-on-disk exception.
"""

import io

from docx import Document


def text_to_docx_bytes(text: str) -> bytes:
    doc = Document()
    for paragraph in text.split("\n"):
        doc.add_paragraph(paragraph)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
