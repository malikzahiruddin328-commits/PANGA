"""Ops tab (2026-08-10): llm_client.call_structured()/call_with_web_search()
now measure their own real wall-clock duration around the API call and log
it via cost_log.log_api_cost()'s new duration_ms field - see
_log_cost()'s docstring for why the caller measures it (only the caller
knows when its own attempt(s) started). Reuses the same fake
streaming-client harness as test_llm_client_error_logging.py."""

from types import SimpleNamespace

from cost_log import load_cost_log
from llm_client import call_structured, call_with_web_search


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
    def __init__(self, final_message):
        self._final_message = final_message

    def stream(self, **kwargs):
        return _FakeStream(self._final_message)

    def create(self, **kwargs):
        return self._final_message


class _FakeClient:
    def __init__(self, final_message):
        self.messages = _FakeMessages(final_message)


def _structured_response():
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text='{"ok": true}')],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


def _web_search_response():
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="a real result")],
        usage=SimpleNamespace(input_tokens=80, output_tokens=40),
    )


def test_call_structured_logs_a_real_measured_duration(isolated_data):
    client = _FakeClient(_structured_response())
    call_structured(client, system="s", user_content="u", schema={}, max_tokens=100, purpose="fit_score")

    entry = load_cost_log()[0]
    assert entry["purpose"] == "fit_score"
    assert isinstance(entry["duration_ms"], float)
    assert entry["duration_ms"] >= 0


def test_call_with_web_search_logs_a_real_measured_duration(isolated_data):
    client = _FakeClient(_web_search_response())
    call_with_web_search(client, system="s", user_content="u", max_tokens=100, purpose="company_lookup")

    entry = load_cost_log()[0]
    assert entry["purpose"] == "company_lookup"
    assert isinstance(entry["duration_ms"], float)
    assert entry["duration_ms"] >= 0
