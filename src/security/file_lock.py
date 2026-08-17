"""Cross-process advisory locking for the JSON stores under data/ (native-
packaging branch, 2026-07-31). Every store here follows a read-modify-write
pattern (load the whole file, mutate, write the whole file back) with no
locking at all - fine while the only writers were a single Streamlit process
plus live Claude Code sessions that ran one at a time, but this branch adds
standalone scheduled scripts that write the same stores unattended, so two
processes can now genuinely race: both read the old state, both write, and
whichever finishes last silently drops the other's update. `locked()` wraps
a store's whole read+merge+write critical section so that can't happen.

Windows-only (msvcrt), matching every other Windows-only assumption already
in this app (keyring's DPAPI backend, run_app.bat).

`lock_dir` param (2026-08-13, global-spend-cap fix): PROJECT_ROOT/_LOCK_DIR
below are computed from THIS file's own on-disk location - fine for every
store that's deliberately per-checkout (jobs.json, applications.json, ...:
each git worktree is supposed to have its own isolated copy for test
isolation), but wrong for a store that must be one genuinely shared,
cross-worktree ledger, like cost_log.json's daily spend cap (see
cost_log.py's own resolution of the real shared data/ directory - a
worktree process running this same file_lock.py still needs to lock the
SAME physical lock file a main-checkout process would use, or the lock
provides no real cross-process protection for that store at all). Passing
`lock_dir` overrides the default per-checkout directory for exactly that
case; omitting it keeps every existing caller's behavior unchanged.
"""

import msvcrt
import time
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOCK_DIR = PROJECT_ROOT / "data" / ".locks"
_MAX_WAIT_SECONDS = 30
_POLL_INTERVAL_SECONDS = 0.05


@contextmanager
def locked(name: str, lock_dir: Path | None = None):
    """Held for the duration of the `with` block. `name` identifies which
    store is being locked (e.g. "jobs", "applications") - each name gets its
    own lock file, so locking one store never blocks an unrelated one.
    Raises TimeoutError if the lock isn't acquired within _MAX_WAIT_SECONDS,
    rather than blocking forever if a prior holder crashed mid-write without
    releasing it.

    lock_dir defaults to this checkout's own data/.locks (unchanged
    behavior for every existing caller) - pass an explicit lock_dir for a
    store that must share one lock file across every git worktree, not
    just within a single checkout (see module docstring)."""
    target_dir = lock_dir if lock_dir is not None else _LOCK_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    lock_path = target_dir / f"{name}.lock"
    with open(lock_path, "a+b") as f:
        deadline = time.monotonic() + _MAX_WAIT_SECONDS
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire the '{name}' data lock within "
                        f"{_MAX_WAIT_SECONDS}s - another process may be "
                        "stuck holding it."
                    )
                time.sleep(_POLL_INTERVAL_SECONDS)
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
