from types import SimpleNamespace

import pytest

from api_cost import estimate_response_cost


def _response(input_tokens, output_tokens, search_blocks=0, cache_creation_input_tokens=None, cache_read_input_tokens=None):
    content = [SimpleNamespace(type="text")] + [
        SimpleNamespace(type="web_search_tool_result") for _ in range(search_blocks)
    ]
    usage_kwargs = dict(input_tokens=input_tokens, output_tokens=output_tokens)
    if cache_creation_input_tokens is not None:
        usage_kwargs["cache_creation_input_tokens"] = cache_creation_input_tokens
    if cache_read_input_tokens is not None:
        usage_kwargs["cache_read_input_tokens"] = cache_read_input_tokens
    return SimpleNamespace(usage=SimpleNamespace(**usage_kwargs), content=content)


def test_token_only_cost_matches_opus5_pricing():
    # 1M input tokens ($5) + 1M output tokens ($25), no searches.
    response = _response(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_response_cost(response, "claude-opus-5") == pytest.approx(30.00)


def test_small_call_cost_is_proportional():
    response = _response(input_tokens=1_000, output_tokens=100)
    expected = 1_000 / 1_000_000 * 5.00 + 100 / 1_000_000 * 25.00
    assert estimate_response_cost(response, "claude-opus-5") == pytest.approx(expected)


def test_web_search_adds_ten_dollars_per_thousand_searches():
    response = _response(input_tokens=0, output_tokens=0, search_blocks=3)
    assert estimate_response_cost(response, "claude-opus-5") == pytest.approx(3 * 0.01)


def test_zero_usage_and_no_searches_is_zero_cost():
    response = _response(input_tokens=0, output_tokens=0)
    assert estimate_response_cost(response, "claude-opus-5") == 0.0


# --- Prompt caching (2026-08-11 fit_score fix) ------------------------------

def test_response_with_no_cache_fields_prices_exactly_as_before():
    # Anthropic's SDK omits cache_creation_input_tokens/cache_read_input_tokens
    # entirely on a response that used no caching - getattr's default must
    # make this identical to the pre-caching cost, not raise or double-count.
    response = _response(input_tokens=1_000, output_tokens=100)
    expected = 1_000 / 1_000_000 * 5.00 + 100 / 1_000_000 * 25.00
    assert estimate_response_cost(response, "claude-opus-5") == pytest.approx(expected)


def test_cache_write_billed_at_1_25x_input_rate():
    # First call in a cache window (cache miss) - Anthropic bills the
    # cached portion at a 25% premium over the base input rate.
    response = _response(input_tokens=100, output_tokens=0, cache_creation_input_tokens=60_000)
    expected = 100 / 1_000_000 * 5.00 + 60_000 / 1_000_000 * 5.00 * 1.25
    assert estimate_response_cost(response, "claude-opus-5") == pytest.approx(expected)


def test_cache_read_billed_at_0_1x_input_rate():
    # A cache hit - the whole point of the fix, and the real 85% reduction
    # measured against today's actual fit_score batch relies on this.
    response = _response(input_tokens=100, output_tokens=0, cache_read_input_tokens=60_000)
    expected = 100 / 1_000_000 * 5.00 + 60_000 / 1_000_000 * 5.00 * 0.10
    assert estimate_response_cost(response, "claude-opus-5") == pytest.approx(expected)


def test_cache_write_and_read_are_never_both_present_but_both_priced_if_they_were():
    # Real Anthropic responses only ever populate one of these per call
    # (a call is either a cache write or a cache read, never both) - this
    # just proves the pricing math doesn't secretly assume mutual
    # exclusivity, so it can't silently drop one pool if that ever changes.
    response = _response(input_tokens=0, output_tokens=0, cache_creation_input_tokens=1_000, cache_read_input_tokens=2_000)
    expected = 1_000 / 1_000_000 * 5.00 * 1.25 + 2_000 / 1_000_000 * 5.00 * 0.10
    assert estimate_response_cost(response, "claude-opus-5") == pytest.approx(expected)


def test_zero_cache_tokens_field_present_costs_nothing_extra():
    # Some real responses may carry the field at 0 rather than omitting it
    # entirely - must not error and must not add cost.
    response = _response(input_tokens=100, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    expected = 100 / 1_000_000 * 5.00
    assert estimate_response_cost(response, "claude-opus-5") == pytest.approx(expected)


def test_unpriced_model_raises_instead_of_silently_underreporting():
    # A genuinely nonexistent model string, not a real Panga-supported one -
    # 2026-08-11: this used "claude-haiku-4-5" as the unpriced example, but
    # that model gained real pricing in the same commit that fixed the real
    # gap this test guards against (sonnet/haiku calls silently failing to
    # log cost during the fit_score model test) - RM caught the test had
    # gone stale in the same commit that fixed the bug it tests for.
    response = _response(input_tokens=1000, output_tokens=100)
    with pytest.raises(KeyError):
        estimate_response_cost(response, "claude-nonexistent-model-9")
