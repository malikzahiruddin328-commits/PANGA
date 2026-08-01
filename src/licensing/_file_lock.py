"""Minimal cross-process advisory lock for the small licensing JSON stores.

Only two realistic concurrent writers exist for these files today (the
foreground app's daily background check, and a one-off uninstaller run),
so this deliberately doesn't pull in a new dependency - exclusive file
creation (O_CREAT | O_EXCL) is atomic on both Windows and POSIX, which is
all a sidecar lock file needs.

Bounded retry, not an unconditional wait: a stuck/orphaned lock file (e.g.
a process killed mid-update) must not hang every future caller forever.
"""

import os
import time
from contextlib import contextmanager
from pathlib import Path

_MAX_WAIT_SECONDS = 5.0
_POLL_INTERVAL_SECONDS = 0.05
_STALE_LOCK_SECONDS = 30.0  # older than this => assume the prior holder died


@contextmanager
def locked(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + _MAX_WAIT_SECONDS
    fd = None
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.monotonic() - lock_path.stat().st_mtime > _STALE_LOCK_SECONDS:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue  # the holder released it between our stat and now
            if time.monotonic() > deadline:
                raise TimeoutError(f"Could not acquire lock {lock_path} within {_MAX_WAIT_SECONDS}s")
            time.sleep(_POLL_INTERVAL_SECONDS)

    try:
        yield
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)
