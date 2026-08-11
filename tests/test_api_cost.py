from types import SimpleNamespace

import pytest

from api_cost import estimate_response_cost


def _response(input_tokens, output_tokens, search_blocks=0):
    content = [SimpleNamespace(type="text")] + [
        SimpleNamespace(type="web_search_tool_result") for _ in range(search_blocks)
    ]
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        content=content,
    )


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
