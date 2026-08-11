"""2026-08-11: a real enforced runtime spend cap (CLAUDE.md's new
"circuit breaker" principle, added after the daily job search scored 455
jobs overnight and spent ~$60 through the already-existing fit_score
pipeline - the cost-blast-radius rule only governs new code being
written, not an already-deployed process overspending once running).

_check_spend_cap() blocks NEW calls once today's real cost_log spend
reaches the cap, called at the top of call_structured/call_with_web_search
before any HTTP request. These tests cover: the cap check itself, the
CRITICAL-once-then-ERROR logging behavior, the cost_log record it leaves,
that call_structured raises while call_with_web_search returns empty
(each function's own existing contract), and that the cap resets on a
new UTC day / doesn't block a call already past the check.
"""

import logging

import pytest

import llm_client
from cost_log import load_cost_log, log_api_cost
from llm_client import LLMSpendCapExceeded, _check_spend_cap, _todays_spend_usd, call_with_web_search


def _seed_todays_spend(amount_usd: float, purpose="fit_score"):
    log_api_cost(purpose=purpose, model="claude-opus-5", input_tokens=1000, output_tokens=1000, cost_usd=amount_usd)


def test_todays_spend_usd_sums_only_todays_entries(isolated_data):
    _seed_todays_spend(5.0)
    _seed_todays_spend(3.0)
    # A stamped entry from a different (past) UTC day must not count.
    from security.crypto_store import write_json
    import cost_log

    entries = load_cost_log()
    entries.append({"timestamp": "2020-01-01T00:00:00+00:00", "cost_usd": 999.0})
    write_json(cost_log.COST_LOG_PATH, entries)

    assert _todays_spend_usd() == 8.0


def test_check_spend_cap_does_nothing_under_the_cap(isolated_data):
    _seed_todays_spend(5.0)
    _check_spend_cap("fit_score")  # must not raise


def test_check_spend_cap_raises_once_the_cap_is_reached(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_DAILY_SPEND_CAP_USD", "10.0")
    _seed_todays_spend(10.0)
    with pytest.raises(LLMSpendCapExceeded) as exc_info:
        _check_spend_cap("fit_score")
    assert "$10.00" in str(exc_info.value)
    assert "resets at UTC midnight" in str(exc_info.value)


def test_check_spend_cap_env_var_overrides_the_default(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_DAILY_SPEND_CAP_USD", "1.0")
    _seed_todays_spend(1.5)
    with pytest.raises(LLMSpendCapExceeded):
        _check_spend_cap("fit_score")


def test_check_spend_cap_ignores_a_malformed_env_var(isolated_data, monkeypatch, caplog):
    monkeypatch.setenv("PANGA_DAILY_SPEND_CAP_USD", "not-a-number")
    _seed_todays_spend(5.0)  # well under the real default of 20.0
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        _check_spend_cap("fit_score")  # must not raise - falls back to the default
    assert any("isn't a valid number" in r.message for r in caplog.records)


def test_check_spend_cap_logs_a_failed_call_entry(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_DAILY_SPEND_CAP_USD", "5.0")
    _seed_todays_spend(5.0)
    with pytest.raises(LLMSpendCapExceeded):
        _check_spend_cap("fit_score")

    entries = load_cost_log()
    blocked = [e for e in entries if e.get("error_type") == "spend_cap_exceeded"]
    assert len(blocked) == 1
    assert blocked[0]["success"] is False
    assert blocked[0]["cost_usd"] == 0.0
    assert blocked[0]["purpose"] == "fit_score"


def test_check_spend_cap_logs_critical_once_then_error_for_repeated_blocks(isolated_data, monkeypatch, caplog):
    monkeypatch.setenv("PANGA_DAILY_SPEND_CAP_USD", "5.0")
    _seed_todays_spend(5.0)
    # monkeypatch (not a raw assignment) so this resets on teardown rather
    # than permanently mutating module state for every later test.
    monkeypatch.setattr(llm_client, "_cap_alarm_logged_for_date", None)

    with caplog.at_level(logging.ERROR, logger="llm_client"):
        with pytest.raises(LLMSpendCapExceeded):
            _check_spend_cap("fit_score")
        with pytest.raises(LLMSpendCapExceeded):
            _check_spend_cap("draft_resume")

    critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR and "Blocked AI call" in r.message]
    assert len(critical_records) == 1
    assert "DAILY SPEND CAP EXCEEDED" in critical_records[0].message
    assert len(error_records) == 1  # the second block, not another CRITICAL


def test_call_with_web_search_returns_empty_when_cap_exceeded_not_raises(isolated_data, monkeypatch):
    # call_with_web_search's own contract: never raises, empty string on
    # any failure - the cap must respect that, not break it.
    monkeypatch.setenv("PANGA_DAILY_SPEND_CAP_USD", "1.0")
    _seed_todays_spend(1.0)

    class _ShouldNeverBeCalled:
        def create(self, **kwargs):
            raise AssertionError("real API call attempted despite the spend cap being exceeded")

    class _FakeClient:
        messages = _ShouldNeverBeCalled()

    text, cost = call_with_web_search(_FakeClient(), system="s", user_content="u", max_tokens=100, purpose="company_lookup")
    assert text == ""
    assert cost == 0.0


def test_call_structured_raises_llm_spend_cap_exceeded(isolated_data, monkeypatch):
    from llm_client import LLMCallFailed, call_structured

    monkeypatch.setenv("PANGA_DAILY_SPEND_CAP_USD", "1.0")
    _seed_todays_spend(1.0)

    class _ShouldNeverBeCalled:
        def stream(self, **kwargs):
            raise AssertionError("real API call attempted despite the spend cap being exceeded")

    class _FakeClient:
        messages = _ShouldNeverBeCalled()

    with pytest.raises(LLMSpendCapExceeded):
        call_structured(_FakeClient(), system="s", user_content="u", schema={}, max_tokens=100, purpose="fit_score")
    # Also catchable as the generic LLMCallFailed every existing caller uses.
    with pytest.raises(LLMCallFailed):
        call_structured(_FakeClient(), system="s", user_content="u", schema={}, max_tokens=100, purpose="fit_score")


def test_spend_cap_does_not_block_a_call_already_past_the_check(isolated_data, monkeypatch):
    # "Finish what's running, halt anything new" - a call that already
    # passed _check_spend_cap must not be re-checked/blocked partway
    # through its own retry loop, even if cost_log's total crosses the
    # cap while it's mid-flight (e.g. a concurrent process logs spend).
    from types import SimpleNamespace

    monkeypatch.setenv("PANGA_DAILY_SPEND_CAP_USD", "100.0")  # comfortably under at check time
    from llm_client import call_structured

    class _FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            # Simulate a concurrent process pushing spend over the cap
            # WHILE this call is already mid-flight (after its own check
            # already passed).
            _seed_todays_spend(500.0)
            return iter([])

        def get_final_message(self):
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text='{"ok": true}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=10),
            )

    class _FakeMessages:
        def stream(self, **kwargs):
            return _FakeStream()

    class _FakeClient:
        messages = _FakeMessages()

    # Must complete successfully - the mid-flight spend spike from the
    # "concurrent process" must not retroactively block this call.
    result = call_structured(_FakeClient(), system="s", user_content="u", schema={}, max_tokens=100, purpose="fit_score")
    assert result == {"ok": True}
