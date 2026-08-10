"""Real gap found live 2026-08-09 (General, then Zahir directly): a real
Claude API call failure (an out-of-credits billing error, in the actual
incident) collapsed into the same generic "Something went wrong talking to
Claude" message every other failure type also produces, with the real cause
nowhere retrievable short of reproducing the API call manually. Two fixes
tested here:
1. A billing/credit-balance error gets a distinct, actionable message.
2. Every real failure path calls logger.error() (caught here via caplog),
   which debug_log.setup_always_on_error_logging() (see test_debug_log.py)
   now always writes to data/logs/panga_debug.log regardless of PANGA_DEBUG.
"""

import logging
from types import SimpleNamespace

import anthropic
import pytest

from llm_client import LLMCallFailed, LLMResponseTruncated, call_structured


class _FakeStream:
    def __init__(self, final_message):
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter([])

    def get_final_message(self):
        return self._final_message


class _FakeMessages:
    def __init__(self, final_message=None, raises=None):
        self._final_message = final_message
        self._raises = raises

    def stream(self, **kwargs):
        if self._raises:
            raise self._raises
        return _FakeStream(self._final_message)


class _FakeClient:
    def __init__(self, final_message=None, raises=None):
        self.messages = _FakeMessages(final_message, raises)


def _response(stop_reason="end_turn", text='{"ok": true}'):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


def _billing_error():
    request = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    response = SimpleNamespace(status_code=400, headers={}, request=request)
    body = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."},
    }
    return anthropic.APIStatusError(
        message=body["error"]["message"], response=response, body=body,
    )


def test_billing_error_gets_a_distinct_actionable_message(isolated_data, caplog):
    client = _FakeClient(raises=_billing_error())
    with pytest.raises(LLMCallFailed) as exc_info:
        call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="draft_resume")
    message = str(exc_info.value)
    assert "out of credits" in message
    assert "console.anthropic.com" in message
    assert "Something went wrong" not in message  # must NOT collapse into the generic catch-all


def test_billing_error_is_logged_with_full_detail(isolated_data, caplog):
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        client = _FakeClient(raises=_billing_error())
        with pytest.raises(LLMCallFailed):
            call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="draft_resume")
    assert any("credit balance" in r.message for r in caplog.records)


def test_refusal_is_logged(isolated_data, caplog):
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        client = _FakeClient(final_message=_response(stop_reason="refusal"))
        with pytest.raises(LLMCallFailed):
            call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="draft_resume")
    assert any("refused" in r.message.lower() for r in caplog.records)


def test_truncation_is_logged_with_the_actual_token_budget(isolated_data, caplog):
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        client = _FakeClient(final_message=_response(stop_reason="max_tokens"))
        with pytest.raises(LLMResponseTruncated):
            call_structured(client, system="s", user_content="u", schema={}, max_tokens=12345, purpose="draft_resume")
    assert any("12345" in r.message for r in caplog.records)


def test_invalid_json_is_logged(isolated_data, caplog):
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        client = _FakeClient(final_message=_response(text="not valid json{"))
        with pytest.raises(LLMCallFailed):
            call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="draft_resume")
    assert any("invalid json" in r.message.lower() or "invalid json" in r.message.lower() for r in caplog.records) or any(
        "raw text" in r.message for r in caplog.records
    )


def test_connection_error_is_logged(isolated_data, caplog):
    with caplog.at_level(logging.ERROR, logger="llm_client"):
        client = _FakeClient(raises=anthropic.APIConnectionError(request=SimpleNamespace()))
        with pytest.raises(LLMCallFailed):
            call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="draft_resume")
    assert any("connection error" in r.message.lower() for r in caplog.records)


def test_a_normal_rate_limit_error_still_gets_the_transient_message(isolated_data, monkeypatch):
    # Must not accidentally get swept into the new billing-specific path.
    import llm_client

    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)  # rate_limit_error retries with real backoff otherwise
    request = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    response = SimpleNamespace(status_code=429, headers={}, request=request)
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "Rate limited."}}
    exc = anthropic.APIStatusError(message=body["error"]["message"], response=response, body=body)

    client = _FakeClient(raises=exc)
    with pytest.raises(LLMCallFailed) as exc_info:
        call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="draft_resume")
    assert "temporarily busy" in str(exc_info.value)
    assert "credits" not in str(exc_info.value)
