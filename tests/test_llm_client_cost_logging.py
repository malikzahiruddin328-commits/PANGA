"""score-first-resume-flow spec item 7: llm_client.call_structured()/
call_with_web_search() now log every real call's cost instead of computing
it and discarding it - see llm_client._log_cost(). These tests exercise
_log_cost() directly against a fake response object (same SimpleNamespace
pattern as test_api_cost.py) rather than mocking the real Anthropic
streaming client, which _log_cost() doesn't depend on at all.
"""

from types import SimpleNamespace

from cost_log import load_cost_log
from llm_client import _log_cost


def _response(input_tokens=1000, output_tokens=200):
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        content=[SimpleNamespace(type="text")],
    )


def test_log_cost_persists_a_real_entry(isolated_data):
    _log_cost(_response(1000, 200), "claude-opus-5", "fit_score", None)
    entries = load_cost_log()
    assert len(entries) == 1
    assert entries[0]["purpose"] == "fit_score"
    assert entries[0]["cost_usd"] == 1000 / 1_000_000 * 5.00 + 200 / 1_000_000 * 25.00


def test_log_cost_passes_through_duration_ms(isolated_data):
    _log_cost(_response(), "claude-opus-5", "fit_score", None, duration_ms=842.1)
    assert load_cost_log()[0]["duration_ms"] == 842.1


def test_log_cost_includes_job_key_when_given(isolated_data):
    _log_cost(_response(), "claude-opus-5", "draft_resume", ("linkedin", "42"))
    entry = load_cost_log()[0]
    assert entry["source"] == "linkedin"
    assert entry["job_id"] == "42"


def test_log_cost_passes_through_real_cache_token_counts(isolated_data):
    # 2026-08-11 fit_score prompt-caching fix - _log_cost must forward the
    # real cache_creation_input_tokens/cache_read_input_tokens from the
    # response into the persisted log entry, not just compute cost from
    # them and discard the breakdown.
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=300, output_tokens=150, cache_read_input_tokens=60_336),
        content=[SimpleNamespace(type="text")],
    )
    _log_cost(response, "claude-opus-5", "fit_score", None)
    entry = load_cost_log()[0]
    assert entry["cache_read_input_tokens"] == 60_336
    assert "cache_creation_input_tokens" not in entry


def test_log_cost_never_raises_on_an_unpriced_model(isolated_data):
    # estimate_response_cost() raises KeyError for a model not in the
    # pricing table - _log_cost() must swallow that (logging a cost is
    # never allowed to break the real API call it's just trying to
    # record), and simply not log an entry.
    _log_cost(_response(), "some-future-unpriced-model", "fit_score", None)
    assert load_cost_log() == []
