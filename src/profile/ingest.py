"""Build step 1: parses source Word documents (per config/document_manifest.yaml)
into raw text, saved locally under data/profile/raw/. This is a text-extraction
pass only — turning the extracted text into structured fields (roles, dates,
skills) happens via the gap-probing interview and later reasoning, not here.
"""

from pathlib import Path
import json

import yaml
from docx import Document

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
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for entry in manifest["documents"]:
        source_path = source_folder / entry["file"]
        text = extract_text(source_path)
        slug = slugify(entry["file"])
        out_path = OUTPUT_DIR / f"{slug}.txt"
        out_path.write_text(text, encoding="utf-8")

        results.append({
            "source_file": entry["file"],
            "category": entry["category"],
            "target_title": entry.get("target_title"),
            "word_count": len(text.split()),
            "extracted_to": str(out_path.relative_to(PROJECT_ROOT)),
        })

    (OUTPUT_DIR / "manifest_result.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    return results


if __name__ == "__main__":
    for r in ingest_all():
        print(f"{r['category']:9} {r['word_count']:5} words  <- {r['source_file']}")
