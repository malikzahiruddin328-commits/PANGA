"""Build step 1: parses source Word documents (per config/document_manifest.yaml)
into raw text, saved locally under data/profile/raw/. This is a text-extraction
pass only — turning the extracted text into structured fields (roles, dates,
skills) happens via the gap-probing interview and later reasoning, not here.
Raw text is encrypted at rest (PRD §7) via security.crypto_store.
"""

from pathlib import Path

import yaml
from docx import Document

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security.crypto_store import write_text, write_json  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "config" / "document_manifest.yaml"
OUTPUT_DIR = PROJECT_ROOT / "data" / "profile" / "raw"


def extract_text(docx_path: Path) -> str:
    doc = Document(docx_path)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def slugify(filename: str) -> str:
    stem = Path(filename).stem
    return "".join(c if c.isalnum() else "_" for c in stem).strip("_").lower()


def ingest_all() -> list[dict]:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    source_folder = Path(manifest["source_folder"])

    results = []
    for entry in manifest["documents"]:
        source_path = source_folder / entry["file"]
        text = extract_text(source_path)
        slug = slugify(entry["file"])
        out_path = OUTPUT_DIR / f"{slug}.txt"
        write_text(out_path, text)

        results.append({
            "source_file": entry["file"],
            "category": entry["category"],
            "target_title": entry.get("target_title"),
            "word_count": len(text.split()),
            "extracted_to": str(out_path.relative_to(PROJECT_ROOT)),
        })

    write_json(OUTPUT_DIR / "manifest_result.json", results)
    return results


if __name__ == "__main__":
    for r in ingest_all():
        print(f"{r['category']:9} {r['word_count']:5} words  <- {r['source_file']}")
