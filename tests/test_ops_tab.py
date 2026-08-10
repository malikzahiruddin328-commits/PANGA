"""New "Ops" tab (2026-08-10, designed with Zahir via Panga Captain) - a
live cost/latency dashboard sourced from cost_log.json. See ops_metrics.py
for the pure computation these tests build on top of (already covered by
test_ops_metrics.py) - these exercise the actual in-app wiring: the tab
appears in the nav, renders real seeded cost_log data correctly, the
period selector works, and empty states don't crash."""

import pytest
from streamlit.testing.v1 import AppTest

from cost_log import log_api_cost

APP_PATH = "src/ui/app.py"


@pytest.fixture
def ops_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    return AppTest.from_file(APP_PATH)


def test_ops_tab_button_exists_in_nav(ops_app):
    at = ops_app
    at.run(timeout=30)
    assert not at.exception
    ops_button = next((b for b in at.button if b.key == "tab_ops"), None)
    assert ops_button is not None
    assert "Ops" in ops_button.proto.label


def test_ops_tab_renders_kpis_from_real_seeded_data(ops_app):
    log_api_cost(purpose="job_score", model="claude-haiku-4-5", input_tokens=980, output_tokens=140, cost_usd=0.003, duration_ms=1100)
    log_api_cost(purpose="web_search", model="claude-sonnet-5", input_tokens=1860, output_tokens=410, cost_usd=0.019, duration_ms=6400)
    log_api_cost(purpose="resume_draft", model="claude-sonnet-5", input_tokens=3140, output_tokens=890, cost_usd=0.041, duration_ms=2900)

    at = ops_app
    at.session_state["active_tab"] = "ops"
    at.run(timeout=30)

    assert not at.exception
    headers = [h.value for h in at.header]
    assert "Ops" in headers

    metric_values = {m.label: m.value for m in at.metric}
    assert metric_values["API calls"] == "3"
    assert metric_values["Spend, 7 days"] == "$0.06"


def test_ops_tab_shows_no_data_yet_when_cost_log_is_empty(ops_app):
    at = ops_app
    at.session_state["active_tab"] = "ops"
    at.run(timeout=30)

    assert not at.exception
    metric_values = {m.label: m.value for m in at.metric}
    assert metric_values["API calls"] == "0"
    # st.metric(value=None) renders as a long dash, not a real None/empty
    # string - confirms the "no data" case degrades gracefully rather than
    # showing a blank or a crash.
    assert metric_values["Avg latency"] not in (None, "")
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "No latency data yet" in markdown_text
    assert "No calls logged in this period yet" in markdown_text


def test_ops_tab_period_selector_defaults_to_7_days(ops_app):
    at = ops_app
    at.session_state["active_tab"] = "ops"
    at.run(timeout=30)

    assert not at.exception
    period_control = next(c for c in at.segmented_control if c.key == "ops_period")
    assert period_control.value == "7 days"


def test_ops_tab_switching_to_today_excludes_older_calls(ops_app):
    from datetime import datetime, timedelta, timezone

    from security.crypto_store import write_json
    import cost_log

    old_entry = {
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(),
        "purpose": "resume_draft", "model": "claude-sonnet-5",
        "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01, "duration_ms": 1000,
    }
    write_json(cost_log.COST_LOG_PATH, [old_entry])
    log_api_cost(purpose="job_score", model="claude-haiku-4-5", input_tokens=10, output_tokens=10, cost_usd=0.001, duration_ms=500)

    at = ops_app
    at.session_state["active_tab"] = "ops"
    at.run(timeout=30)
    metric_values_before = {m.label: m.value for m in at.metric}
    assert metric_values_before["API calls"] == "2"  # both fall inside the default 7-day window

    period_control = next(c for c in at.segmented_control if c.key == "ops_period")
    period_control.set_value("Today")
    at.run(timeout=30)

    assert not at.exception
    metric_values_after = {m.label: m.value for m in at.metric}
    assert metric_values_after["API calls"] == "1"  # only the recent one is within "Today"


def test_recent_calls_table_shows_real_call_data(ops_app):
    log_api_cost(purpose="web_search", model="claude-sonnet-5", input_tokens=1860, output_tokens=410, cost_usd=0.019, duration_ms=6400)

    at = ops_app
    at.session_state["active_tab"] = "ops"
    at.run(timeout=30)

    assert not at.exception
    found = False
    for d in at.dataframe:
        df = d.value
        if "Purpose" in df.columns:
            found = True
            assert "web_search" in df["Purpose"].tolist()
            assert "6.4s" in df["Duration"].tolist()
            assert "$0.019" in df["Cost"].tolist()
    assert found


def test_avg_latency_by_purpose_empty_state_does_not_crash(ops_app):
    # Cost-log entries with no duration_ms (as if logged before this field
    # existed) - the chart must show its own empty state, not crash.
    log_api_cost(purpose="job_score", model="claude-haiku-4-5", input_tokens=10, output_tokens=10, cost_usd=0.001)

    at = ops_app
    at.session_state["active_tab"] = "ops"
    at.run(timeout=30)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "No latency data yet" in markdown_text
