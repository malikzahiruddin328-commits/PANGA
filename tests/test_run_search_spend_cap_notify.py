"""2026-08-11 fast-follow: run_search.py's daily notification now reports
if llm_client's spend cap tripped today, so Zahir doesn't have to
separately check the Ops tab or panga_debug.log to learn a batch scoring
run was cut short. Covers notify()'s own formatting (spend-cap line first,
survives truncation, fires even with nothing else to report) and
llm_client.spend_cap_tripped_today()'s real query against cost_log.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_search  # noqa: E402

from cost_log import log_api_cost  # noqa: E402
from llm_client import spend_cap_tripped_today  # noqa: E402


@pytest.fixture
def fake_notify(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(run_search, "send_notification", mock)
    return mock


def test_notify_reports_spend_cap_hit_first(fake_notify):
    run_search.notify([], 0, spend_cap_hit=True)
    fake_notify.assert_called_once()
    title, message = fake_notify.call_args[0][:2]
    assert "spend cap was hit today" in message
    assert message.startswith("Daily AI spend cap")


def test_notify_sends_nothing_when_nothing_to_report(fake_notify):
    run_search.notify([], 0, spend_cap_hit=False)
    fake_notify.assert_not_called()


def test_notify_fires_purely_for_a_spend_cap_hit_with_no_other_news(fake_notify):
    # Before this fix, "not parts: return" meant a cap-hit day with zero
    # strong matches and zero unreviewed reasons sent NOTHING at all -
    # arguably the worst case, since Zahir would have no idea anything
    # was wrong. Now it must still notify.
    run_search.notify([], 0, spend_cap_hit=True)
    fake_notify.assert_called_once()


def test_notify_combines_spend_cap_with_other_news(fake_notify):
    matches = [{"title": "VP Eng", "organization": "Acme", "fit_score": 88}]
    run_search.notify(matches, 2, spend_cap_hit=True)
    message = fake_notify.call_args[0][1]
    assert "spend cap was hit today" in message
    assert "strong new match" in message
    assert "rejection reason" in message


def test_notify_spend_cap_line_survives_200_char_truncation(fake_notify):
    # A long list of strong matches could otherwise push the cap warning
    # past message[:200] if it weren't listed first.
    matches = [
        {"title": f"Very Long Executive Title Number {i}", "organization": f"Some Fairly Long Company Name {i}", "fit_score": 90}
        for i in range(10)
    ]
    run_search.notify(matches, 0, spend_cap_hit=True)
    message = fake_notify.call_args[0][1]
    assert len(message) <= 200
    assert "spend cap was hit today" in message


def test_notify_omits_spend_cap_line_when_not_hit(fake_notify):
    matches = [{"title": "VP Eng", "organization": "Acme", "fit_score": 88}]
    run_search.notify(matches, 0, spend_cap_hit=False)
    message = fake_notify.call_args[0][1]
    assert "spend cap" not in message.lower()


def test_spend_cap_tripped_today_is_false_with_no_entries(isolated_data):
    assert spend_cap_tripped_today() is False


def test_spend_cap_tripped_today_is_true_after_a_real_block(isolated_data):
    log_api_cost(
        purpose="fit_score", model="none", input_tokens=0, output_tokens=0, cost_usd=0.0,
        success=False, error_type="spend_cap_exceeded", attempt_count=0, models_tried=[],
    )
    assert spend_cap_tripped_today() is True


def test_spend_cap_tripped_today_ignores_other_failure_types(isolated_data):
    # A genuine API failure (overloaded_error) is a different thing - must
    # not be mistaken for a spend-cap trip.
    log_api_cost(
        purpose="fit_score", model="claude-opus-5", input_tokens=0, output_tokens=0, cost_usd=0.0,
        success=False, error_type="overloaded_error", attempt_count=3, models_tried=["claude-opus-5"] * 3,
    )
    assert spend_cap_tripped_today() is False


def test_spend_cap_tripped_today_ignores_a_trip_from_a_different_day(isolated_data):
    from security.crypto_store import write_json
    import cost_log

    write_json(cost_log.COST_LOG_PATH, [{
        "timestamp": "2020-01-01T00:00:00+00:00", "purpose": "fit_score", "model": "none",
        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "success": False,
        "error_type": "spend_cap_exceeded", "attempt_count": 0, "models_tried": [],
    }])
    assert spend_cap_tripped_today() is False
