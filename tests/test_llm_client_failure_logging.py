"""2026-08-10 audit fix #26: a call that exhausts retries and model
fallback without ever getting a response now gets its own cost_log entry
(success=False) - before this, only successful calls ever reached
cost_log, so real failed-call volume/attempt history was invisible to
every cost/ops analysis. Reuses the fake streaming-client harness from
test_llm_client_error_logging.py.
"""

from types import SimpleNamespace

import anthropic
import pytest

from cost_log import load_cost_log
from llm_client import LLMCallFailed, _log_failed_call, call_structured, call_with_web_search


class _FakeMessages:
    def __init__(self, raises=None):
        self._raises = raises

    def stream(self, **kwargs):
        raise self._raises

    def create(self, **kwargs):
        raise self._raises


class _FakeClient:
    def __init__(self, raises=None):
        self.messages = _FakeMessages(raises)


def _overloaded_everywhere():
    request = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    response = SimpleNamespace(status_code=529, headers={}, request=request)
    body = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    return anthropic.APIStatusError(message="Overloaded", response=response, body=body)


def test_log_failed_call_persists_a_failure_entry(isolated_data):
    exc = _overloaded_everywhere()
    exc._panga_attempt_count = 4
    exc._panga_models_tried = ["claude-opus-5", "claude-opus-5", "claude-opus-5", "claude-sonnet-5"]
    exc._panga_request_ms = 842.0

    _log_failed_call(exc, "fit_score", None)

    entries = load_cost_log()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["success"] is False
    assert entry["purpose"] == "fit_score"
    assert entry["model"] == "claude-sonnet-5"  # the LAST model actually tried
    assert entry["cost_usd"] == 0.0
    assert entry["input_tokens"] == 0
    assert entry["output_tokens"] == 0
    assert entry["duration_ms"] == 842.0
    assert entry["error_type"] == "overloaded_error"
    assert entry["attempt_count"] == 4
    assert entry["models_tried"] == ["claude-opus-5", "claude-opus-5", "claude-opus-5", "claude-sonnet-5"]


def test_log_failed_call_includes_job_key(isolated_data):
    exc = _overloaded_everywhere()
    exc._panga_attempt_count = 1
    exc._panga_models_tried = ["claude-opus-5"]
    exc._panga_request_ms = 100.0

    _log_failed_call(exc, "draft_resume", ("linkedin", "42"))
    entry = load_cost_log()[0]
    assert entry["source"] == "linkedin"
    assert entry["job_id"] == "42"


def test_log_failed_call_never_raises_even_without_attempt_info(isolated_data):
    # An exception that never went through _call_with_retries (so has none
    # of the _panga_* attributes) must still log something sane, not crash.
    exc = _overloaded_everywhere()
    _log_failed_call(exc, "fit_score", None)
    entry = load_cost_log()[0]
    assert entry["attempt_count"] == 1
    assert entry["models_tried"] == ["unknown"]


def test_call_structured_exhausted_failure_logs_a_failed_call_entry(isolated_data, monkeypatch):
    import llm_client

    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)
    client = _FakeClient(raises=_overloaded_everywhere())

    with pytest.raises(LLMCallFailed):
        call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="fit_score")

    entries = load_cost_log()
    assert len(entries) == 1
    assert entries[0]["success"] is False
    assert entries[0]["purpose"] == "fit_score"
    assert entries[0]["attempt_count"] == 4  # 3 primary + 1 fallback, all overloaded
    assert entries[0]["error_type"] == "overloaded_error"


def test_call_with_web_search_exhausted_failure_logs_a_failed_call_entry(isolated_data, monkeypatch):
    import llm_client

    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)
    client = _FakeClient(raises=_overloaded_everywhere())

    text, cost = call_with_web_search(client, system="s", user_content="u", max_tokens=100, purpose="company_lookup")
    assert text == ""
    assert cost == 0.0

    entries = load_cost_log()
    assert len(entries) == 1
    assert entries[0]["success"] is False
    assert entries[0]["purpose"] == "company_lookup"


def test_a_non_retryable_first_attempt_failure_still_logs_exactly_one_attempt(isolated_data, monkeypatch):
    import llm_client

    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)
    request = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    response = SimpleNamespace(status_code=400, headers={}, request=request)
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad schema"}}
    exc = anthropic.APIStatusError(message="bad schema", response=response, body=body)
    client = _FakeClient(raises=exc)

    with pytest.raises(LLMCallFailed):
        call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="fit_score")

    entry = load_cost_log()[0]
    assert entry["attempt_count"] == 1
    assert entry["models_tried"] == [llm_client.DEFAULT_MODEL]
