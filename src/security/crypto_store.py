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
import time
from pathlib import Path

import keyring
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SERVICE_NAME = os.environ.get("PANGA_KEYRING_SERVICE", "Panga")
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
    """Same transient-Windows-PermissionError retry as write_bytes()'s
    os.replace() call, found by this fix's own regression test: a reader's
    open() can momentarily race the OS-level file swap during a concurrent
    write_bytes() and see a transient PermissionError, not because the file
    is genuinely locked, but because the atomic replace is mid-flight on
    Windows. Retries a few times with a short backoff before giving up -
    still raises if the file is genuinely inaccessible (e.g. a real
    permissions problem, not a swap in progress)."""
    last_exc = None
    for attempt in range(20):
        if not path.exists():
            return None
        try:
            return decrypt_bytes(path.read_bytes())
        except (PermissionError, FileNotFoundError) as exc:
            last_exc = exc
            time.sleep(min(0.03 * (attempt + 1), 0.25))
    raise last_exc


def write_bytes(path: Path, data: bytes) -> None:
    """Writes atomically: encrypts fully in memory, writes the ciphertext to
    a sibling temp file, then os.replace()s it onto `path` in one OS-level
    operation.

    Real crash, 2026-08-17: the previous version did `path.write_bytes(...)`
    directly, which opens the file in 'wb' mode - truncating it to zero
    bytes BEFORE the new content is written. Every read of this store
    (read_bytes/read_json, used by job_store.load_jobs(),
    applications.load_applications(), etc.) takes no lock at all, on the
    documented assumption that "reads don't need the lock, only
    read-modify-write does" - true only if a write is atomic from a
    reader's point of view. It wasn't: a concurrent read landing in the
    truncate-then-write window could see an empty or partially-written
    file, fail decryption, and raise an uncaught exception. This matters in
    practice, not just in theory - a background job-search run (locked
    writes via security.file_lock, several minutes long) and the live
    Streamlit session read the same jobs.json/applications.json
    concurrently, and 8510 crashed during exactly that overlap the same
    day this was found. os.replace() is atomic on both Windows and POSIX -
    a concurrent reader now always sees either the fully-old or the
    fully-new file, never a partial one - so every existing unlocked read
    call site becomes safe without needing to touch any of them.

    Retry note (found writing this fix's own regression test, real and
    reproducible, not theoretical): unlike POSIX rename(), Windows'
    MoveFileEx (what os.replace() uses under the hood there) can raise
    PermissionError [WinError 5] "Access is denied" if a reader happens to
    hold `path` open at the exact instant of replace - a real, transient
    condition, not a permanent failure; it clears as soon as that reader's
    handle closes, which is typically milliseconds later given every read
    in this codebase is a single open-read-close with no held handles. A
    short bounded retry absorbs that window without masking a genuinely
    stuck/locked file (e.g. antivirus scanning, another process holding an
    exclusive lock) - it still raises after retries are exhausted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ciphertext = encrypt_bytes(data)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        tmp_path.write_bytes(ciphertext)
        last_exc = None
        for attempt in range(20):
            try:
                os.replace(tmp_path, path)
                last_exc = None
                break
            except PermissionError as exc:
                last_exc = exc
                time.sleep(min(0.03 * (attempt + 1), 0.25))
        if last_exc is not None:
            raise last_exc
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


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
