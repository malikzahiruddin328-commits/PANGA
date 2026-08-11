"""2026-08-10 adversarial self-audit of llm_client's retry/backoff/fallback
logic - covers what _call_with_retries actually measures and records, not
just its final return value:

1. request_ms sums real per-attempt time only, excluding backoff sleep
   (audit finding #28 - the old wall-clock measurement counted artificial
   wait as "latency").
2. An exhausted failure carries honest attempt_count/models_tried, not
   just the last exception's own detail (audit finding #29).
3. get_client() disables the Anthropic SDK's own hidden retry (audit
   finding #27 - it was stacking with _call_with_retries, empirically
   confirmed to turn one "exhausted" call into 12 real HTTP requests).
"""

from types import SimpleNamespace

import anthropic
import pytest

import llm_client
from llm_client import _RetryResult, _call_with_retries, get_client


def _status_error(status_code, error_type, message="boom"):
    request = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    response = SimpleNamespace(status_code=status_code, headers={}, request=request)
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    return anthropic.APIStatusError(message=message, response=response, body=body)


def test_get_client_disables_the_sdks_own_hidden_retry(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-for-test")
    client = get_client()
    assert client.max_retries == 0


def test_request_ms_sums_real_attempt_time_not_backoff_sleep(monkeypatch):
    # Real (but small) backoff sleep - NOT stubbed to a no-op, since the
    # whole point is proving request_ms excludes genuinely-elapsed sleep
    # time, not just that it's small when sleep never really happens.
    monkeypatch.setattr(llm_client, "_BACKOFF_BASE_SECONDS", 0.25)

    calls = []

    def make_request(model):
        calls.append(model)
        if len(calls) == 1:
            raise _status_error(529, "overloaded_error")
        return "OK"

    import time as time_module
    wall_clock_start = time_module.perf_counter()
    result = _call_with_retries(make_request, "claude-opus-5")
    wall_clock_ms = (time_module.perf_counter() - wall_clock_start) * 1000

    assert isinstance(result, _RetryResult)
    assert result.attempt_count == 2
    assert result.models_tried == ["claude-opus-5", "claude-opus-5"]
    # The real backoff sleep (0.25s = 250ms) genuinely elapsed - wall_clock_ms
    # proves that. request_ms must NOT include it: the two fake requests
    # are near-instant, so request_ms should be tiny while wall_clock_ms is
    # dominated by the real sleep.
    assert wall_clock_ms >= 250
    assert result.request_ms < 50
    assert result.request_ms < wall_clock_ms / 2


def test_exhausted_failure_carries_honest_attempt_count_and_models_tried(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)

    def make_request(model):
        raise _status_error(529, "overloaded_error")

    with pytest.raises(anthropic.APIStatusError) as exc_info:
        _call_with_retries(make_request, "claude-opus-5")

    exc = exc_info.value
    # 3 primary attempts (_MAX_ATTEMPTS) + 1 fallback attempt, all failed.
    assert exc._panga_attempt_count == 4
    assert exc._panga_models_tried == [
        "claude-opus-5", "claude-opus-5", "claude-opus-5", llm_client.FALLBACK_MODEL,
    ]
    assert exc._panga_request_ms >= 0


def test_non_transient_failure_on_first_attempt_reports_exactly_one_attempt(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)

    def make_request(model):
        raise _status_error(400, "invalid_request_error")

    with pytest.raises(anthropic.APIStatusError) as exc_info:
        _call_with_retries(make_request, "claude-opus-5")

    exc = exc_info.value
    assert exc._panga_attempt_count == 1
    assert exc._panga_models_tried == ["claude-opus-5"]


def test_rate_limit_exhaustion_does_not_attempt_fallback(monkeypatch):
    # Only overloaded_error triggers the fallback attempt - a persistent
    # rate limit should NOT try the fallback model (it's a distinct
    # capacity signal, not "this specific model is down").
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)
    calls = []

    def make_request(model):
        calls.append(model)
        raise _status_error(429, "rate_limit_error")

    with pytest.raises(anthropic.APIStatusError) as exc_info:
        _call_with_retries(make_request, "claude-opus-5")

    assert calls == ["claude-opus-5"] * 3  # no fallback model in the call list
    assert exc_info.value._panga_attempt_count == 3


def test_real_httpx_transport_confirms_max_retries_0_means_one_request_per_attempt(monkeypatch):
    # Not a mock of our own code - a real anthropic.Anthropic client with a
    # real (mocked-at-the-transport-level) HTTP call, confirming the SDK
    # itself makes exactly one request when max_retries=0, closing the
    # stacking gap from audit finding #27 empirically, not just by
    # inspecting max_retries.
    import httpx

    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        return httpx.Response(
            529,
            json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
            headers={"request-id": "req_test"},
        )

    client = anthropic.Anthropic(
        api_key="sk-fake", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(anthropic.APIStatusError):
        client.messages.create(model="claude-opus-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}])

    assert call_count["n"] == 1
