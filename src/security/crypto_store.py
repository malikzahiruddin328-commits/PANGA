"""Encryption at rest for data/ (PRD §7): resume text, interview answers,
job history, applications, and CTA emails.

Key model: a random 256-bit key is generated on first use and held in the
OS credential store via `keyring` (Windows Credential Manager/DPAPI on this
machine; Keychain if this ever runs on a Mac) - not a typed passphrase.
Scheduled tasks (panga-daily-job-search, panga-gmail-cta-scan,
panga-cta-fulfillment) run unattended several times a day with no one
present to type one, so the key has to unlock automatically for this OS
login instead. This protects data/ if the files are copied off this
machine or the disk is stolen/imaged; it does not protect against someone
else using this same OS login (see PRD §7).
"""

import json
import os
from pathlib import Path

import keyring
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SERVICE_NAME = "Panga"
_KEY_USERNAME = "data-encryption-key"
_NONCE_LEN = 12
_MAGIC = b"PANGAENC1"  # lets callers (e.g. the one-time migration script) tell
                        # ciphertext apart from not-yet-migrated plaintext
                        # without guessing from content.


def is_encrypted(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("rb") as f:
        return f.read(len(_MAGIC)) == _MAGIC


def _get_or_create_key() -> bytes:
    stored = keyring.get_password(_SERVICE_NAME, _KEY_USERNAME)
    if stored is not None:
        return bytes.fromhex(stored)
    key = AESGCM.generate_key(bit_length=256)
    keyring.set_password(_SERVICE_NAME, _KEY_USERNAME, key.hex())
    return key


def encrypt_bytes(plaintext: bytes) -> bytes:
    aesgcm = AESGCM(_get_or_create_key())
    nonce = os.urandom(_NONCE_LEN)
    return _MAGIC + nonce + aesgcm.encrypt(nonce, plaintext, None)


def decrypt_bytes(blob: bytes) -> bytes:
    if blob[:len(_MAGIC)] != _MAGIC:
        raise ValueError("Data is not in Panga's encrypted format (missing header) - was it migrated?")
    blob = blob[len(_MAGIC):]
    aesgcm = AESGCM(_get_or_create_key())
    nonce, ciphertext = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag as e:
        raise ValueError(
            f"Could not decrypt data - the encryption key in this Windows "
            f"account's credential store doesn't match what this file was "
            f"encrypted with (wrong machine/account, or a corrupted file?)."
        ) from e


def read_bytes(path: Path) -> bytes | None:
    if not path.exists():
        return None
    return decrypt_bytes(path.read_bytes())


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_bytes(data))


def read_json(path: Path, default=None):
    raw = read_bytes(path)
    return default if raw is None else json.loads(raw.decode("utf-8"))


def write_json(path: Path, data) -> None:
    write_bytes(path, json.dumps(data, indent=2).encode("utf-8"))


def read_text(path: Path, default: str | None = None) -> str | None:
    raw = read_bytes(path)
    return default if raw is None else raw.decode("utf-8")


def write_text(path: Path, content: str) -> None:
    write_bytes(path, content.encode("utf-8"))
