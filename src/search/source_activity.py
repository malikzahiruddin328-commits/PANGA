"""Source-level activity tracking (2026-08-07), added alongside the board-
scope-expansion work per Zahir's explicit ask: freshness_check.py already
tells you when one POSTING closes, but nothing told you when a whole BOARD
quietly stopped producing new listings. Rigzone's own volume drop (found
during this same work - confirmed live at ~4-6 postings site-wide, vs.
"thousands unfiltered" at its original 2026-08-04 recon) is exactly the
case this exists to catch automatically going forward, instead of needing
another manual recon pass to notice.

Persists a rolling history of each real run's "how many NEW jobs did this
source add" count - every search_*() function in run_search.py already
computes this via job_store.save_jobs()'s return value, this just keeps it
instead of discarding it after logging.

A source is flagged stale only after MIN_CONSECUTIVE_ZERO_RUNS consecutive
REAL (non-error) runs added zero new jobs - same fail-safe "unknown is not
the same as stale" philosophy freshness_check.py already uses for
individual postings: a run that errored (blocked, network failure, site
down for a day) doesn't count as evidence either way and is simply
skipped, not treated as a zero: a source that's actually fine but had one
bad-network day shouldn't get flagged, and a source that's genuinely dead
shouldn't get a free pass just because one check happened to error.

Human-surfaced, not auto-acted-on: is_source_stale() feeds a hint shown in
the Settings tab next to "Manage companies" - keeps the merit call human
(same reasoning behind the Rigzone decision itself: dropping it was a
product call worth making explicitly, not something to have decided
silently), just gives real data to make that call with instead of a
one-time recon snapshot that goes stale itself.
"""

import json
from pathlib import Path

from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIVITY_PATH = PROJECT_ROOT / "data" / "source_activity.json"

MIN_CONSECUTIVE_ZERO_RUNS = 5  # roughly a work-week of daily runs
MAX_HISTORY_PER_SOURCE = 30  # bounded, not an ever-growing per-source log


def _load() -> dict:
    if not ACTIVITY_PATH.exists():
        return {}
    with open(ACTIVITY_PATH, encoding="utf-8") as f:
        return json.load(f) or {}


def _save(data: dict) -> None:
    ACTIVITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVITY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def record_run_result(source: str, added_count: int, had_error: bool = False) -> None:
    """Called once per source per run_search.py run. had_error=True means
    this run's added_count isn't trustworthy evidence either way (e.g.
    every request for this source failed/errored) - still recorded (for
    visibility), but excluded from the consecutive-zero-runs streak
    is_source_stale() looks at."""
    with locked("source_activity"):
        data = _load()
        history = data.setdefault(source, [])
        history.append({"added": added_count, "had_error": had_error})
        del history[:-MAX_HISTORY_PER_SOURCE]
        _save(data)


def is_source_stale(source: str, min_consecutive_zero_runs: int = MIN_CONSECUTIVE_ZERO_RUNS) -> bool | None:
    """True only once min_consecutive_zero_runs consecutive REAL (non-
    error) runs each added 0 new jobs. None if there's no history yet, or
    not yet enough real runs to judge - a source with only 2 real runs on
    record is "unknown", not "not stale", since 2 zero-runs isn't enough
    evidence to call it dead but shouldn't silently read as healthy
    either."""
    data = _load()
    history = data.get(source)
    if not history:
        return None
    real_runs = [h for h in history if not h["had_error"]]
    if len(real_runs) < min_consecutive_zero_runs:
        return None
    recent = real_runs[-min_consecutive_zero_runs:]
    return all(h["added"] == 0 for h in recent)


def all_tracked_sources() -> list[str]:
    return sorted(_load().keys())
