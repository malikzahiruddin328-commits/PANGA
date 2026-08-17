"""Generic adaptive concurrency throttle (2026-08-17).

Gives a caller a bounded worker count for a batch of independent, expensive
tasks (subprocess calls, API calls, anything that's real local CPU/IO load)
that adapts DOWNWARD when the local machine is already under real measured
load, instead of always saturating it with a fixed worker count.

Deliberately decoupled from any notion of "job", "application record",
"basket", etc. - see this package's own __init__.py docstring. The only
thing this module knows about is "how many of these can I run right now."
Reusable as-is by any project (Zahir's explicit ask, 2026-08-17: he wants
this in C2 and other future projects too, not glued to Panga).

Design, deliberately simple: check real CPU load ONCE, right before a batch
of tasks starts, and hand back a worker count for that whole batch.
ThreadPoolExecutor's own pool size is fixed once created, and resizing a
LIVE pool mid-run is a materially harder problem this doesn't attempt to
solve - a caller that wants the throttle to react to a load change mid-run
should chunk its work and call effective_worker_count() again before each
chunk, not expect one long-lived pool to resize itself.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a real requirements.txt dependency; this is only a safety net for an environment where it somehow isn't installed
    psutil = None

# Tuned conservatively for a real dev machine that's often ALSO running a
# Streamlit process, live `claude` CLI subprocess calls, and other Claude
# Code sessions at the same time (Panga's own CLAUDE.md documents exactly
# this multi-process reality) - a load that's already high before a new
# batch even starts is a real signal, not noise.
DEFAULT_HIGH_LOAD_PERCENT = 85.0
DEFAULT_MODERATE_LOAD_PERCENT = 70.0
DEFAULT_MIN_WORKERS = 1
DEFAULT_CPU_SAMPLE_SECONDS = 0.2


def current_cpu_percent(sample_seconds: float = DEFAULT_CPU_SAMPLE_SECONDS) -> float | None:
    """Real, system-wide CPU utilization sampled over `sample_seconds` - a
    deliberately BLOCKING call (psutil.cpu_percent(interval=...), not the
    non-blocking "since the last call anywhere in this process" variant),
    since a caller checking once before a batch starts wants one honest
    fresh reading, not a stale/undefined-baseline average left over from
    whenever cpu_percent() last happened to be called elsewhere.

    Returns None (never raises) if psutil isn't importable or the read
    itself fails, so callers can tell "checked, load is fine" apart from
    "couldn't check at all" and fail OPEN on the latter - see
    effective_worker_count()."""
    if psutil is None:
        return None
    try:
        return psutil.cpu_percent(interval=sample_seconds)
    except Exception:
        logger.exception("adaptive_throttle: failed to read CPU load, failing open (no throttling applied)")
        return None


def effective_worker_count(
    target_max: int,
    min_workers: int = DEFAULT_MIN_WORKERS,
    high_load_percent: float = DEFAULT_HIGH_LOAD_PERCENT,
    moderate_load_percent: float = DEFAULT_MODERATE_LOAD_PERCENT,
    sample_seconds: float = DEFAULT_CPU_SAMPLE_SECONDS,
    cpu_percent: float | None = None,
) -> int:
    """How many workers a batch of up-to-`target_max` concurrent tasks
    should actually use RIGHT NOW, based on one real CPU-load sample taken
    at call time (or `cpu_percent`, if the caller already has a fresh
    reading and wants to skip a second blocking sample - e.g. a test, or a
    caller that samples once and reuses it for several decisions in the
    same instant).

    Never returns more than target_max. Never returns less than
    min(min_workers, target_max) (so a caller that only ever wants 1-2
    workers is never throttled below what it asked for in the first
    place). Never raises - any failure to read load fails OPEN (returns
    target_max, fully unthrottled) rather than silently stalling real work
    over a monitoring problem.

    Three load bands, checked against one fresh sample:
    - load >= high_load_percent: back off hard, to min_workers - the
      machine is already busy (another process/session doing real CPU
      work right now).
    - moderate_load_percent <= load < high_load_percent: ease off without
      fully serializing - half of target_max, rounded up, floored at
      min_workers.
    - load < moderate_load_percent, or load couldn't be read at all:
      target_max, fully unthrottled."""
    target_max = max(1, int(target_max))
    min_workers = max(1, min(int(min_workers), target_max))
    load = cpu_percent if cpu_percent is not None else current_cpu_percent(sample_seconds)
    if load is None:
        return target_max
    if load >= high_load_percent:
        return min_workers
    if load >= moderate_load_percent:
        return max(min_workers, (target_max + 1) // 2)
    return target_max
