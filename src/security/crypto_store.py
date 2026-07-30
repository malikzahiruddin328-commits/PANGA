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

Recovery (PRD §13 "Windows-account-loss recovery", §15): if this Windows
account's credential store ever loses the key (profile corruption, or
data/ deliberately moved to a new machine), the key is gone and data/
becomes permanently unreadable with no other copy - see
generate_recovery_code()/recover_key_with_code() below for the escape
hatch. This does NOT re-encrypt data/ itself; it just wraps a second,
recoverable copy of the same key.
"""

import base64
import binascii
import hashlib
import json
import os
import secrets
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECOVERY_ENVELOPE_PATH = PROJECT_ROOT / "data" / "security" / "recovery_envelope.json"
_RECOVERY_CODE_BYTES = 20  # 160 bits - random, not a memorized passphrase, so
                            # KDF slowness is defense-in-depth, not the primary defense.
_RECOVERY_KDF_ITERATIONS = 600_000


def is_encrypted(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("rb") as f:
        return f.read(len(_MAGIC)) == _MAGIC


def _get_or_create_key() -> bytes:
    stored = keyring.get_password(_SERVICE_NAME, _KEY_USERNAME)
    if stored is not None:
        return bytes.fromhex(stored)

    if RECOVERY_ENVELOPE_PATH.exists():
        # A recovery envelope only ever gets created after a real key existed
        # (see generate_recovery_code) - so its presence with no matching
        # keyring entry means this key went missing (not a fresh install),
        # most likely this account/profile lost its credential store, or
        # data/ was copied here from elsewhere. Minting a fresh key here
        # would silently create a key that can never decrypt the existing
        # files - fail loudly and point at recovery instead.
        raise RuntimeError(
            "No encryption key found in this Windows account's credential "
            "store, but a recovery envelope exists at "
            f"{RECOVERY_ENVELOPE_PATH} - this looks like data/ survived "
            "from a different Windows account/profile. Run "
            "scripts/recover_access.py with your saved recovery code "
            "before doing anything else, or the existing data will "
            "become unreadable."
        )

    key = AESGCM.generate_key(bit_length=256)
    keyring.set_password(_SERVICE_NAME, _KEY_USERNAME, key.hex())
    return key


def _format_recovery_code(code_bytes: bytes) -> str:
    b32 = base64.b32encode(code_bytes).decode("ascii")  # 32 chars for 20 bytes, no padding
    return "-".join(b32[i:i + 4] for i in range(0, len(b32), 4))


def _parse_recovery_code(code_str: str) -> bytes:
    cleaned = code_str.strip().upper().replace("-", "").replace(" ", "")
    try:
        return base64.b32decode(cleaned)
    except (binascii.Error, ValueError) as e:
        raise ValueError("That doesn't look like a valid recovery code - check for typos and try again.") from e


def has_recovery_code() -> bool:
    return RECOVERY_ENVELOPE_PATH.exists()


def generate_recovery_code() -> str:
    """Wraps the current data-encryption key under a freshly generated
    recovery code and saves the wrapped copy to RECOVERY_ENVELOPE_PATH.
    Returns the code in its display form - this is the ONLY time it's ever
    available; nothing about it is retained anywhere. The caller is
    responsible for showing it to the user with a "write this down now"
    warning. Calling this again replaces the envelope (and invalidates any
    previously generated code) with one wrapping the same current key."""
    dek = _get_or_create_key()

    code_bytes = secrets.token_bytes(_RECOVERY_CODE_BYTES)
    salt = os.urandom(16)
    wrap_key = hashlib.pbkdf2_hmac("sha256", code_bytes, salt, _RECOVERY_KDF_ITERATIONS, dklen=32)

    aesgcm = AESGCM(wrap_key)
    nonce = os.urandom(_NONCE_LEN)
    wrapped_key = aesgcm.encrypt(nonce, dek, None)

    RECOVERY_ENVELOPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECOVERY_ENVELOPE_PATH.write_text(json.dumps({
        "version": 1,
        "kdf": "pbkdf2_sha256",
        "iterations": _RECOVERY_KDF_ITERATIONS,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "wrapped_key": wrapped_key.hex(),
    }, indent=2), encoding="utf-8")

    return _format_recovery_code(code_bytes)


def recover_key_with_code(code_str: str) -> None:
    """Unwraps the data-encryption key using a code from generate_recovery_code()
    and reinstalls it into this account's credential store, restoring normal
    (no-code-needed) access from then on. Raises ValueError on a malformed or
    incorrect code, or if no recovery envelope exists at all."""
    if not RECOVERY_ENVELOPE_PATH.exists():
        raise ValueError(f"No recovery envelope found at {RECOVERY_ENVELOPE_PATH} - no recovery code has ever been generated for this data.")

    code_bytes = _parse_recovery_code(code_str)
    envelope = json.loads(RECOVERY_ENVELOPE_PATH.read_text(encoding="utf-8"))
    salt = bytes.fromhex(envelope["salt"])
    nonce = bytes.fromhex(envelope["nonce"])
    wrapped_key = bytes.fromhex(envelope["wrapped_key"])

    wrap_key = hashlib.pbkdf2_hmac("sha256", code_bytes, salt, envelope["iterations"], dklen=32)
    try:
        dek = AESGCM(wrap_key).decrypt(nonce, wrapped_key, None)
    except InvalidTag as e:
        raise ValueError("That recovery code doesn't match - check for typos and try again.") from e

    keyring.set_password(_SERVICE_NAME, _KEY_USERNAME, dek.hex())


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
