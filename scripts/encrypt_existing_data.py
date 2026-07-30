"""One-time migration: encrypts plaintext files already sitting under data/
in place, now that all read/write paths go through security.crypto_store
(PRD §7). Safe to re-run - files already in Panga's encrypted format
(detected via the magic header, not content-sniffing) are skipped.

For each plaintext file: encrypts to a sibling temp file, decrypts that temp
file back and compares to the original bytes to confirm the round-trip is
correct, then atomically replaces the original. Never leaves a half-written
file in place if something goes wrong partway through.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from security.crypto_store import encrypt_bytes, decrypt_bytes, is_encrypted  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

# Only files Panga's store modules actually read/write - not, e.g., stray
# notes someone might drop under data/ by hand.
TARGET_FILES = [
    DATA_DIR / "profile" / "structured" / "master_profile.json",
    DATA_DIR / "profile" / "raw" / "manifest_result.json",
    DATA_DIR / "jobs" / "jobs.json",
    DATA_DIR / "applications" / "applications.json",
    DATA_DIR / "cta_emails" / "cta_emails.json",
    *sorted((DATA_DIR / "profile" / "raw").glob("*.txt")),
]


def migrate_file(path: Path) -> str:
    if not path.exists():
        return "skipped (missing)"
    if is_encrypted(path):
        return "skipped (already encrypted)"

    plaintext = path.read_bytes()
    ciphertext = encrypt_bytes(plaintext)

    tmp_path = path.with_suffix(path.suffix + ".encrypting")
    tmp_path.write_bytes(ciphertext)

    roundtrip = decrypt_bytes(tmp_path.read_bytes())
    if roundtrip != plaintext:
        tmp_path.unlink()
        raise RuntimeError(f"Round-trip check failed for {path} - original left untouched")

    os.replace(tmp_path, path)
    return f"encrypted ({len(plaintext)} bytes)"


def main() -> None:
    for path in TARGET_FILES:
        rel = path.relative_to(PROJECT_ROOT)
        result = migrate_file(path)
        print(f"{rel}: {result}")


if __name__ == "__main__":
    main()
