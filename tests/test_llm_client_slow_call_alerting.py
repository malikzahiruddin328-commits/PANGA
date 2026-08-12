"""2026-08-11: Zahir hit a real 9m45s resume-generation call live and asked
for systematic latency logging - "we should be logging everything and
these are the things we should be catching. Remember Performance." Real
call duration was already captured for every call before this (both
success via _log_cost and failure via _log_failed_call) - the actual gap
was that nothing surfaced an outlier unless someone happened to open the
Ops tab and notice a slow bar. _flag_if_slow() closes that: once a single
call crosses SLOW_CALL_THRESHOLD_MS, it logs an ERROR-level line (always
written to panga_debug.log) and fires an immediate system-tray
notification. slowest_call_today() lets run_search.py's own daily
notification report the day's slowest flagged call too.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import pytest

from cost_log import log_api_cost
from llm_client import (
    SLOW_CALL_THRESHOLD_MS,
    LLMCallFailed,
    _flag_if_slow,
    call_structured,
    call_with_web_search,
    slowest_call_today,
)


class _FakeStream:
    def __init__(self, text: str):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter([])

    def get_final_message(self):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


class _FakeMessages:
    def __init__(self, text=None, raises=None):
        self._text = text
        self._raises = raises

    def stream(self, **kwargs):
        if self._raises:
            raise self._raises
        return _FakeStream(self._text)

    def create(self, **kwargs):
        if self._raises:
            raise self._raises
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(input_tokens=80, output_tokens=40),
        )


class _FakeClient:
    def __init__(self, text=None, raises=None):
        self.messages = _FakeMessages(text, raises)


def _overloaded_everywhere():
    request = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    response = SimpleNamespace(status_code=529, headers={}, request=request)
    body = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    return anthropic.APIStatusError(message="Overloaded", response=response, body=body)


@pytest.fixture
def fake_notify(monkeypatch):
    mock = MagicMock()
    import notifications

    monkeypatch.setattr(notifications, "send_notification", mock)
    return mock


def _fake_perf_counter_sequence(monkeypatch, module, deltas):
    """Makes module.time.perf_counter() return a controlled sequence of
    values, so a "slow call" can be simulated without a real multi-second
    sleep - each pair of consecutive calls in _call_with_retries brackets
    one real attempt, so deltas are the elapsed time each bracket should
    report."""
    values = [0.0]
    for d in deltas:
        values.append(values[-1] + d)
    it = iter(values)
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(it))


def test_flag_if_slow_does_nothing_below_threshold(isolated_data, fake_notify, caplog):
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        _flag_if_slow("fit_score", "claude-opus-5", None, SLOW_CALL_THRESHOLD_MS - 1)
    assert len(caplog.records) == 0
    fake_notify.assert_not_called()


def test_flag_if_slow_does_nothing_for_none_duration(isolated_data, fake_notify, caplog):
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        _flag_if_slow("fit_score", "claude-opus-5", None, None)
    assert len(caplog.records) == 0
    fake_notify.assert_not_called()


def test_flag_if_slow_logs_and_notifies_at_threshold(isolated_data, fake_notify, caplog):
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        _flag_if_slow("draft_resume", "claude-opus-5", ("linkedin", "42"), SLOW_CALL_THRESHOLD_MS)
    assert any("SLOW AI CALL" in r.message for r in caplog.records)
    assert any("draft_resume" in r.message for r in caplog.records)
    fake_notify.assert_called_once()
    title, message = fake_notify.call_args[0][:2]
    assert "Slow AI call" in title
    assert "draft_resume" in message


def test_flag_if_slow_reports_real_seconds_in_the_notification(isolated_data, fake_notify):
    _flag_if_slow("interview_prep_generate", "claude-opus-5", None, 585_000.0)  # the real 9m45s case
    message = fake_notify.call_args[0][1]
    assert "585s" in message


def test_flag_if_slow_swallows_its_own_failures(isolated_data, monkeypatch, caplog):
    import notifications

    monkeypatch.setattr(notifications, "send_notification", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        _flag_if_slow("fit_score", "claude-opus-5", None, SLOW_CALL_THRESHOLD_MS)  # must not raise
    assert any("Failed to flag slow AI call" in r.message for r in caplog.records)


def test_call_structured_success_path_flags_a_slow_call(isolated_data, monkeypatch, fake_notify):
    import llm_client

    _fake_perf_counter_sequence(monkeypatch, llm_client, [SLOW_CALL_THRESHOLD_MS / 1000])
    client = _FakeClient(text='{"ok": true}')

    result = call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="draft_resume")
    assert result == {"ok": True}
    fake_notify.assert_called_once()
    assert "draft_resume" in fake_notify.call_args[0][1]


def test_call_structured_success_path_does_not_flag_a_fast_call(isolated_data, monkeypatch, fake_notify):
    import llm_client

    _fake_perf_counter_sequence(monkeypatch, llm_client, [1.0])  # 1 real second, well under threshold
    client = _FakeClient(text='{"ok": true}')

    call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="draft_resume")
    fake_notify.assert_not_called()


def test_call_structured_exhausted_failure_still_flags_if_slow(isolated_data, monkeypatch, fake_notify):
    import llm_client

    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)
    # 4 real attempts (3 primary + 1 fallback), each brackets its own
    # started_at/elapsed pair of perf_counter() reads - give each attempt
    # enough elapsed time that the SUM crosses the threshold. Gaps of 1000
    # between attempts are arbitrary (that time isn't measured - it falls
    # between one attempt's end-read and the next attempt's start-read).
    per_attempt = (SLOW_CALL_THRESHOLD_MS / 1000) / 4 + 1
    values = []
    base = 0.0
    for _ in range(4):
        values.append(base)
        values.append(base + per_attempt)
        base += 1000.0
    it = iter(values)
    monkeypatch.setattr(llm_client.time, "perf_counter", lambda: next(it))
    client = _FakeClient(raises=_overloaded_everywhere())

    with pytest.raises(LLMCallFailed):
        call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="fit_score")

    fake_notify.assert_called_once()
    assert "fit_score" in fake_notify.call_args[0][1]


def test_call_with_web_search_success_path_flags_a_slow_call(isolated_data, monkeypatch, fake_notify):
    import llm_client

    _fake_perf_counter_sequence(monkeypatch, llm_client, [SLOW_CALL_THRESHOLD_MS / 1000])
    client = _FakeClient(text="found it")

    call_with_web_search(client, system="s", user_content="u", max_tokens=100, purpose="company_website_lookup")
    fake_notify.assert_called_once()
    assert "company_website_lookup" in fake_notify.call_args[0][1]


def test_slowest_call_today_returns_none_with_no_entries(isolated_data):
    assert slowest_call_today() is None


def test_slowest_call_today_ignores_calls_under_the_threshold(isolated_data):
    log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.01, duration_ms=5000.0)
    assert slowest_call_today() is None


def test_slowest_call_today_returns_the_slowest_flagged_entry(isolated_data):
    log_api_cost(purpose="draft_resume", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.01, duration_ms=70_000.0)
    log_api_cost(purpose="interview_prep_generate", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.01, duration_ms=585_000.0)
    log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.01, duration_ms=65_000.0)

    slowest = slowest_call_today()
    assert slowest is not None
    assert slowest["purpose"] == "interview_prep_generate"
    assert slowest["duration_ms"] == 585_000.0


def test_slowest_call_today_includes_a_flagged_failed_call(isolated_data):
    log_api_cost(
        purpose="fit_score", model="claude-opus-5", input_tokens=0, output_tokens=0, cost_usd=0.0,
        success=False, error_type="overloaded_error", attempt_count=4,
        models_tried=["claude-opus-5"] * 3 + ["claude-sonnet-5"], duration_ms=90_000.0,
    )
    slowest = slowest_call_today()
    assert slowest is not None
    assert slowest["purpose"] == "fit_score"
    assert slowest["success"] is False


def test_slowest_call_today_ignores_a_flagged_call_from_a_different_day(isolated_data):
    from security.crypto_store import write_json
    import cost_log

    write_json(cost_log.COST_LOG_PATH, [{
        "timestamp": "2020-01-01T00:00:00+00:00", "purpose": "draft_resume", "model": "claude-opus-5",
        "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.01, "duration_ms": 585_000.0, "success": True,
    }])
    assert slowest_call_today() is None
