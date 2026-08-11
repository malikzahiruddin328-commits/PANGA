"""Ops tab (2026-08-10, first cut - designed with Zahir via Panga Captain).
Pure computation over cost_log.py's real per-call log: period filtering,
KPI aggregation, per-purpose latency breakdown, and the spend-over-time
series. No new instrumentation lives here - duration_ms is measured at
the real call sites (llm_client.py) and persisted by cost_log.py; this
module only reads and aggregates what's already logged.

Deliberately out of scope for this slice (per the confirmed spec): rerun
timing and structured error-rate views - their storage pattern isn't
settled yet.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import pandas as pd

SLOW_LATENCY_THRESHOLD_MS = 3000.0

PERIODS = ["Today", "7 days", "30 days"]
PERIOD_HOURS = {"Today": 24, "7 days": 24 * 7, "30 days": 24 * 30}


def _parse_ts(entry: dict) -> datetime | None:
    raw = entry.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def filter_by_window(entries: list[dict], hours_ago_start: float, hours_ago_end: float = 0) -> list[dict]:
    """Entries with timestamp in [now - hours_ago_start, now - hours_ago_end)
    - a rolling window, same convention as prospector/kpis.py's _recent()
    (rolling from datetime.now(), not calendar-boundary-aligned).
    hours_ago_end=0 means "up to now"."""
    now = datetime.now(timezone.utc)
    lower = now - timedelta(hours=hours_ago_start)
    upper = now - timedelta(hours=hours_ago_end)
    return [e for e in entries if (when := _parse_ts(e)) and lower <= when < upper]


def filter_by_period(entries: list[dict], period: str) -> list[dict]:
    hours = PERIOD_HOURS.get(period, PERIOD_HOURS["7 days"])
    return filter_by_window(entries, hours)


def prior_period(entries: list[dict], period: str) -> list[dict]:
    """The same-length window immediately BEFORE the selected period, so
    the AVG LATENCY card can show a real "vs prior period" delta rather
    than an isolated number."""
    hours = PERIOD_HOURS.get(period, PERIOD_HOURS["7 days"])
    return filter_by_window(entries, hours * 2, hours)


def _successful(entries: list[dict]) -> list[dict]:
    """Entries missing "success" entirely (every entry logged before the
    2026-08-10 failed-call-logging fix) default to True - so this filter
    is a no-op against every entry that predates the field, and existing
    KPI numbers don't shift underneath anyone. See llm_client._log_failed_call
    for what a success=False entry looks like."""
    return [e for e in entries if e.get("success", True)]


def compute_kpis(current: list[dict], prior: list[dict]) -> dict:
    """Headline KPI-strip numbers for the selected period. Spend/call-count/
    latency are computed from SUCCESSFUL calls only (_successful) - a
    failed call has $0 cost and no real generation latency to average in,
    so including it would silently distort both spend-per-call and
    latency stats without saying so. failed_call_count is the one new
    number surfaced from the full (unfiltered) current list - real failed-
    call volume is now visible in the data even though this first cut
    doesn't yet render it anywhere (see ops_metrics.py module docstring:
    structured error-rate views are a deliberate fast-follow).

    Durations only come from entries with a real duration_ms - calls
    logged before this field existed are excluded from latency stats (not
    treated as 0, which would silently understate every average)."""
    failed_call_count = sum(1 for e in current if not e.get("success", True))
    current = _successful(current)
    prior = _successful(prior)

    spend_usd = sum(e.get("cost_usd") or 0 for e in current)
    call_count = len(current)

    durations = [e["duration_ms"] for e in current if e.get("duration_ms") is not None]
    avg_latency_ms = (sum(durations) / len(durations)) if durations else None
    p95_latency_ms = float(pd.Series(durations).quantile(0.95)) if durations else None

    prior_durations = [e["duration_ms"] for e in prior if e.get("duration_ms") is not None]
    prior_avg_ms = (sum(prior_durations) / len(prior_durations)) if prior_durations else None
    avg_latency_delta_ms = (
        avg_latency_ms - prior_avg_ms
        if avg_latency_ms is not None and prior_avg_ms is not None
        else None
    )

    p95_note = None
    if p95_latency_ms is not None:
        tail_purposes = [e.get("purpose", "unspecified") for e in current if (e.get("duration_ms") or 0) >= p95_latency_ms]
        if tail_purposes:
            top_purpose, top_count = Counter(tail_purposes).most_common(1)[0]
            verb = "dominate" if top_count / len(tail_purposes) > 0.5 else "appear most often in"
            p95_note = f"{top_purpose} calls {verb} the tail"

    return {
        "spend_usd": spend_usd,
        "call_count": call_count,
        "avg_latency_ms": avg_latency_ms,
        "p95_latency_ms": p95_latency_ms,
        "avg_latency_delta_ms": avg_latency_delta_ms,
        "p95_note": p95_note,
        "failed_call_count": failed_call_count,
    }


def spend_by_day(entries: list[dict], period: str) -> list[dict]:
    """One row per bucket for the "Spend over time" chart, oldest first -
    daily buckets for 7-day/30-day periods (weekday labels for 7 days,
    MM/DD for 30 to avoid repeating weekday names), hourly buckets for
    "Today" (a single daily bucket wouldn't make a chart). Every bucket in
    the window is included even at $0, so a quiet stretch shows as a real
    flat line rather than a compressed gap. Successful calls only - a
    failed call is always logged at $0 cost, so this filter is currently
    a no-op numerically, but stays explicit in case a future partial-
    billing case (see llm_client._log_failed_call) ever gives a failure a
    real nonzero cost."""
    filtered = _successful(filter_by_period(entries, period))
    now = datetime.now(timezone.utc)

    if period == "Today":
        hour_totals = defaultdict(float)
        for e in filtered:
            when = _parse_ts(e)
            if when:
                hour_totals[when.replace(minute=0, second=0, microsecond=0)] += e.get("cost_usd") or 0
        points = []
        for i in range(23, -1, -1):
            bucket = (now - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
            points.append({"label": bucket.strftime("%H:00"), "spend": round(hour_totals.get(bucket, 0.0), 4)})
        return points

    days = 7 if period == "7 days" else 30
    day_totals = defaultdict(float)
    for e in filtered:
        when = _parse_ts(e)
        if when:
            day_totals[when.date()] += e.get("cost_usd") or 0
    points = []
    for i in range(days - 1, -1, -1):
        day = (now - timedelta(days=i)).date()
        label = day.strftime("%a") if days <= 7 else day.strftime("%m/%d")
        points.append({"label": label, "spend": round(day_totals.get(day, 0.0), 4)})
    return points


def avg_latency_by_purpose(entries: list[dict], period: str) -> list[dict]:
    """One row per purpose with a real duration_ms logged, sorted slowest
    first - the "Avg latency by purpose" bar chart's data. is_slow flags
    the bar for the >=3.0s highlight (same threshold as the table).
    Successful calls only - mixing in failed calls (which may have $0
    cost but real retry-driven duration, or a near-zero duration from an
    immediate non-retryable failure) would change what this average
    actually measures without saying so."""
    filtered = _successful(filter_by_period(entries, period))
    by_purpose = defaultdict(list)
    for e in filtered:
        duration = e.get("duration_ms")
        if duration is not None:
            by_purpose[e.get("purpose") or "unspecified"].append(duration)

    rows = []
    for purpose, durations in by_purpose.items():
        avg_ms = sum(durations) / len(durations)
        rows.append({
            "purpose": purpose,
            "avg_duration_s": avg_ms / 1000,
            "is_slow": avg_ms >= SLOW_LATENCY_THRESHOLD_MS,
        })
    rows.sort(key=lambda r: r["avg_duration_s"], reverse=True)
    return rows


def recent_calls(entries: list[dict], period: str) -> list[dict]:
    """Every SUCCESSFUL call in the selected period, newest first - the
    "Recent calls" table's data, unfiltered/unformatted (the caller
    applies display formatting). Failed calls are deliberately excluded
    here (not just uncounted) - showing a $0.00/0-token row next to real
    calls with no visual distinction would misread as a free instant
    success. The failure data itself is fully present in cost_log.json
    for direct analysis (see compute_kpis' failed_call_count); a proper
    failed-calls view in this table is a real UI design decision (icon,
    color, separate section) deliberately left for a follow-up, not
    built without a mockup."""
    filtered = _successful(filter_by_period(entries, period))
    return sorted(filtered, key=lambda e: e.get("timestamp") or "", reverse=True)
