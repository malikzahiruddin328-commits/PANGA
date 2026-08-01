"""Stable per-install device fingerprint.

PLACEHOLDER pending native-packaging's installed-app shape (see
docs/licensing-scope.md "Explicit dependencies" — that branch needs to
settle before this is final). For now this generates a random id once and
caches it encrypted alongside the rest of the license state, which is
sufficient to prove "same install, second launch" but says nothing about
the underlying hardware - it will NOT survive a reinstall into a fresh
data/ directory, and native-packaging may want something hardware-derived
instead (survives reinstall, doesn't survive a factory-reset image, etc).
Swap _generate() when that's decided; callers only ever see get_device_id().
"""

import uuid
from pathlib import Path

from security import crypto_store

from licensing._file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEVICE_ID_PATH = PROJECT_ROOT / "data" / "security" / "device_id.json"


def _generate() -> str:
    return str(uuid.uuid4())


def get_device_id() -> str:
    cached = crypto_store.read_json(DEVICE_ID_PATH, default=None)
    if cached is not None:
        return cached["device_id"]
    # Locked read-then-write: two concurrent first-ever launches must not
    # each mint and persist a different id, since every server-side record
    # keys off whichever one wins.
    with locked(DEVICE_ID_PATH):
        cached = crypto_store.read_json(DEVICE_ID_PATH, default=None)
        if cached is not None:
            return cached["device_id"]
        device_id = _generate()
        crypto_store.write_json(DEVICE_ID_PATH, {"device_id": device_id})
        return device_id
