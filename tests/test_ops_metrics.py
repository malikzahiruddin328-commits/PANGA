"""Ops tab (2026-08-10): pure computation over cost_log entries. These
tests build entries with explicit, hand-stamped timestamps (relative to
"now") rather than relying on real wall-clock timing, so period-boundary
behavior is deterministic."""

from datetime import datetime, timedelta, timezone

import ops_metrics


def _entry(hours_ago, purpose="fit_score", cost_usd=0.01, duration_ms=1000.0, **kwargs):
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"timestamp": timestamp, "purpose": purpose, "cost_usd": cost_usd, "duration_ms": duration_ms, **kwargs}


def test_filter_by_period_today_excludes_older_entries():
    entries = [_entry(1), _entry(12), _entry(25), _entry(200)]
    result = ops_metrics.filter_by_period(entries, "Today")
    assert len(result) == 2


def test_filter_by_period_7_days():
    entries = [_entry(1), _entry(24 * 3), _entry(24 * 6.9), _entry(24 * 8)]
    result = ops_metrics.filter_by_period(entries, "7 days")
    assert len(result) == 3


def test_filter_by_period_30_days():
    entries = [_entry(24 * 5), _entry(24 * 29), _entry(24 * 31)]
    result = ops_metrics.filter_by_period(entries, "30 days")
    assert len(result) == 2


def test_prior_period_is_the_window_immediately_before_the_current_one():
    entries = [
        _entry(1, cost_usd=0.10),      # in current 7-day window
        _entry(24 * 5, cost_usd=0.20),  # in current 7-day window
        _entry(24 * 10, cost_usd=0.30),  # in prior 7-day window (7-14 days ago)
        _entry(24 * 20, cost_usd=0.40),  # outside both windows
    ]
    prior = ops_metrics.prior_period(entries, "7 days")
    assert len(prior) == 1
    assert prior[0]["cost_usd"] == 0.30


def test_entries_with_unparseable_or_missing_timestamps_are_excluded_not_crashed_on():
    entries = [_entry(1), {"timestamp": "not-a-real-timestamp", "cost_usd": 1}, {"cost_usd": 1}]
    result = ops_metrics.filter_by_period(entries, "7 days")
    assert len(result) == 1


def test_compute_kpis_spend_and_call_count():
    current = [_entry(1, cost_usd=1.5), _entry(2, cost_usd=2.5)]
    kpis = ops_metrics.compute_kpis(current, [])
    assert kpis["spend_usd"] == 4.0
    assert kpis["call_count"] == 2


def test_compute_kpis_avg_latency_excludes_entries_with_no_duration():
    current = [_entry(1, duration_ms=1000), _entry(2, duration_ms=3000), _entry(3, duration_ms=None)]
    kpis = ops_metrics.compute_kpis(current, [])
    assert kpis["avg_latency_ms"] == 2000


def test_compute_kpis_avg_latency_is_none_with_no_duration_data_at_all():
    current = [_entry(1, duration_ms=None), _entry(2, duration_ms=None)]
    kpis = ops_metrics.compute_kpis(current, [])
    assert kpis["avg_latency_ms"] is None
    assert kpis["p95_latency_ms"] is None


def test_compute_kpis_delta_is_negative_when_latency_improved():
    current = [_entry(1, duration_ms=1000)]
    prior = [_entry(24 * 10, duration_ms=2000)]
    kpis = ops_metrics.compute_kpis(current, prior)
    assert kpis["avg_latency_delta_ms"] == -1000


def test_compute_kpis_delta_is_none_without_prior_data():
    current = [_entry(1, duration_ms=1000)]
    kpis = ops_metrics.compute_kpis(current, [])
    assert kpis["avg_latency_delta_ms"] is None


def test_compute_kpis_p95_note_names_the_dominant_tail_purpose():
    # web_search calls are the slow ones - should dominate the p95 tail.
    current = (
        [_entry(1, purpose="job_score", duration_ms=500) for _ in range(10)]
        + [_entry(1, purpose="web_search", duration_ms=8000) for _ in range(3)]
    )
    kpis = ops_metrics.compute_kpis(current, [])
    assert kpis["p95_note"] is not None
    assert "web_search" in kpis["p95_note"]


def test_spend_by_day_today_returns_24_hourly_buckets_summing_to_the_real_total():
    entries = [_entry(1, cost_usd=1.0), _entry(5, cost_usd=2.0), _entry(30, cost_usd=99.0)]  # last one outside "Today"
    points = ops_metrics.spend_by_day(entries, "Today")
    assert len(points) == 24
    assert round(sum(p["spend"] for p in points), 4) == 3.0


def test_spend_by_day_7_days_returns_7_daily_buckets_with_weekday_labels():
    entries = [_entry(1, cost_usd=1.0), _entry(24 * 3, cost_usd=2.0)]
    points = ops_metrics.spend_by_day(entries, "7 days")
    assert len(points) == 7
    assert round(sum(p["spend"] for p in points), 4) == 3.0
    weekday_abbrevs = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    assert all(p["label"] in weekday_abbrevs for p in points)


def test_spend_by_day_30_days_returns_30_daily_buckets_with_date_labels():
    points = ops_metrics.spend_by_day([_entry(1, cost_usd=1.0)], "30 days")
    assert len(points) == 30
    assert all("/" in p["label"] for p in points)


def test_avg_latency_by_purpose_sorted_slowest_first_and_flags_slow():
    entries = [
        _entry(1, purpose="job_score", duration_ms=500),
        _entry(1, purpose="web_search", duration_ms=5800),
        _entry(1, purpose="resume_draft", duration_ms=2700),
    ]
    rows = ops_metrics.avg_latency_by_purpose(entries, "7 days")
    assert [r["purpose"] for r in rows] == ["web_search", "resume_draft", "job_score"]
    assert rows[0]["is_slow"] is True  # 5.8s >= 3.0s threshold
    assert rows[1]["is_slow"] is False  # 2.7s < 3.0s
    assert round(rows[0]["avg_duration_s"], 1) == 5.8


def test_avg_latency_by_purpose_excludes_entries_with_no_duration():
    entries = [_entry(1, purpose="job_score", duration_ms=None)]
    rows = ops_metrics.avg_latency_by_purpose(entries, "7 days")
    assert rows == []


def test_avg_latency_by_purpose_averages_across_multiple_calls_of_the_same_purpose():
    entries = [
        _entry(1, purpose="job_score", duration_ms=1000),
        _entry(2, purpose="job_score", duration_ms=3000),
    ]
    rows = ops_metrics.avg_latency_by_purpose(entries, "7 days")
    assert len(rows) == 1
    assert rows[0]["avg_duration_s"] == 2.0


def test_recent_calls_sorted_newest_first():
    entries = [_entry(5, purpose="old"), _entry(1, purpose="new"), _entry(3, purpose="middle")]
    calls = ops_metrics.recent_calls(entries, "7 days")
    assert [c["purpose"] for c in calls] == ["new", "middle", "old"]


def test_recent_calls_excludes_calls_outside_the_selected_period():
    entries = [_entry(1, purpose="in_window"), _entry(24 * 10, purpose="out_of_window")]
    calls = ops_metrics.recent_calls(entries, "7 days")
    assert [c["purpose"] for c in calls] == ["in_window"]


def test_slow_latency_threshold_is_3_seconds():
    assert ops_metrics.SLOW_LATENCY_THRESHOLD_MS == 3000.0
