"""Bounded-retry tracking, shared by scripts/gmail_cta_scan.py and
scripts/job_alert_scan.py (2026-08-09, real bug found by Backlog's audit
against the live code): neither script marked a message reviewed on
every terminal path, so a message whose reasoning call kept failing (a
persistently malformed body, a schema Claude can't satisfy) got
re-processed - a real AI call, real cost - forever, every single run.

The fix can't just be "always mark reviewed on failure too" - that would
give a message exactly one shot: a single transient failure (an API
blip, a momentary rate limit) would permanently exclude it from ever
being classified/extracted, which is worse than the original bug for the
common case. It also can't be "never mark reviewed on failure" (the
original bug) - that's unbounded retry, unbounded cost, for a message
that will keep failing the same way forever.

This module is the middle ground: track a per-message failure count, and
only give up (let the caller mark it reviewed) once a message has failed
MAX_ATTEMPTS consecutive times. A message that eventually succeeds has
its count cleared, so a flaky message that failed once or twice before
finally going through doesn't carry a stale count forward.

Keyed by (scan_name, provider, account, ref) rather than just (provider,
account, ref): gmail_cta_scan.py's classify_thread() and
job_alert_scan.py's extract_listings() are different reasoning calls
over the same underlying message - a failure in one says nothing about
whether the other would also fail, so they get independent counts rather
than sharing (and potentially prematurely exhausting) one budget.

Plain JSON, not security.crypto_store - a message ref and a failure
count aren't sensitive, same call as config/job_sources.yaml. Locked via
security.file_lock.locked() since both scheduled scan scripts and (in
principle) a live app session could touch this concurrently, same
shared-store reasoning as every other store in this codebase."""

from pathlib import Path

from security.crypto_store import read_json, write_json
from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parent
RETRY_DIR = PROJECT_ROOT.parent / "data" / "scan_retries"

# After this many consecutive failures for the same message under the
# same scan, give up and let the caller mark it reviewed rather than
# retry forever - see module docstring. 3 gives a message two genuine
# extra chances beyond the first failure (transient blips rarely repeat
# 3 runs in a row) without letting a permanently-broken message burn a
# real AI call indefinitely.
MAX_ATTEMPTS = 3


def _path(scan_name: str) -> Path:
    return RETRY_DIR / f"{scan_name}.json"


def _key(provider: str, account: str, ref: str) -> str:
    return f"{provider}:{account}:{ref}"


def record_failure(scan_name: str, provider: str, account: str, ref: str) -> int:
    """Increments and returns this message's failure count for this scan.
    Caller should give up (mark the message reviewed) once the returned
    count reaches MAX_ATTEMPTS."""
    path = _path(scan_name)
    with locked(f"scan_retries_{scan_name}"):
        data = read_json(path, default={})
        key = _key(provider, account, ref)
        data[key] = data.get(key, 0) + 1
        write_json(path, data)
        return data[key]


def clear_failure(scan_name: str, provider: str, account: str, ref: str) -> None:
    """Called once a message is successfully processed (classified/
    extracted without error), so a prior failure count doesn't linger -
    a message shouldn't need fewer future retries just because it failed
    once, long ago, before eventually succeeding. No-op if there was no
    recorded failure (the common case - most messages never fail)."""
    path = _path(scan_name)
    key = _key(provider, account, ref)
    with locked(f"scan_retries_{scan_name}"):
        data = read_json(path, default={})
        if key in data:
            del data[key]
            write_json(path, data)
