"""2026-08-11 (systematic latency logging, Zahir hit a real 9m45s resume-
generation call live): run_search.py's daily notification now also
reports the day's slowest AI call, if any crossed llm_client.
SLOW_CALL_THRESHOLD_MS - the individual call already gets its own
immediate system-tray notification the moment it happens (see
llm_client._flag_if_slow), this is the same "don't make Zahir go
looking" summary-level addition the spend cap got in test_run_search_
spend_cap_notify.py.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_search  # noqa: E402


@pytest.fixture
def fake_notify(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(run_search, "send_notification", mock)
    return mock


def _slowest_entry(purpose="draft_resume", duration_ms=585_000.0):
    return {"purpose": purpose, "model": "claude-opus-5", "duration_ms": duration_ms, "success": True}


def test_notify_reports_slowest_call(fake_notify):
    run_search.notify([], 0, slowest_call=_slowest_entry())
    fake_notify.assert_called_once()
    message = fake_notify.call_args[0][1]
    assert "Slowest AI call today" in message
    assert "draft_resume" in message
    assert "585s" in message


def test_notify_sends_nothing_when_no_slow_call(fake_notify):
    run_search.notify([], 0, slowest_call=None)
    fake_notify.assert_not_called()


def test_notify_combines_slow_call_with_other_news(fake_notify):
    matches = [{"title": "VP Eng", "organization": "Acme", "fit_score": 88}]
    run_search.notify(matches, 2, slowest_call=_slowest_entry())
    message = fake_notify.call_args[0][1]
    assert "Slowest AI call" in message
    assert "strong new match" in message
    assert "rejection reason" in message


def test_notify_combines_slow_call_with_spend_cap(fake_notify):
    run_search.notify([], 0, spend_cap_hit=True, slowest_call=_slowest_entry())
    message = fake_notify.call_args[0][1]
    assert "spend cap was hit today" in message
    assert "Slowest AI call" in message
    # Spend cap listed first (existing convention) - still present when
    # combined with the new slow-call line.
    assert message.index("spend cap") < message.index("Slowest AI call")


def test_notify_omits_slow_call_line_when_nothing_flagged(fake_notify):
    matches = [{"title": "VP Eng", "organization": "Acme", "fit_score": 88}]
    run_search.notify(matches, 0, slowest_call=None)
    message = fake_notify.call_args[0][1]
    assert "slowest" not in message.lower()
