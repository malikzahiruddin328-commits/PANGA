"""Real per-call cost accounting for direct Anthropic API calls (Zahir's
explicit ask 2026-07-31: "make a function of this we will be using this in
other places as well"). Computed from each response's actual usage, never
an estimate - every direct-API module in Panga (tailoring/drafting.py,
prospector/company_lookup.py, prospector/prospector_score.py, ...) should
call this instead of reimplementing the token/search math per module.
"""

# $/1M tokens, per model - only claude-opus-5 was used anywhere in Panga
# when this table was first written (see tailoring/drafting.py's
# DEFAULT_MODEL), but keyed by model so a future second model doesn't
# silently get priced as Opus 5.
#
# Real gap found 2026-08-11 (fit_score cheaper-model test): sonnet/haiku
# were missing entirely, so every real API call made against them during
# the test silently failed to log its cost - estimate_response_cost()
# raised KeyError, and llm_client._log_cost()'s own broad
# "except Exception: logger.exception(...)" swallowed it (by design, so a
# logging failure never breaks the real API call - see that function's
# docstring), which converted this module's own "fail loudly rather than
# silently under-report" intent into exactly the silent under-report it
# was written to prevent. Added claude-sonnet-5/claude-haiku-4-5 rates so
# this stops happening for the two models Panga is actively testing.
# claude-sonnet-5 uses Anthropic's introductory rate ($2/$10 per MTok,
# in effect through 2026-08-31) - update to the standard $3/$15 after
# that date.
_TOKEN_PRICING_PER_MTOK = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
}
_WEB_SEARCH_PER_SEARCH = 10.00 / 1000

# Anthropic's documented ephemeral (5-minute TTL) prompt-caching multipliers
# on the model's base input rate - a cache write costs MORE than a normal
# input token (you're paying to populate the cache), a cache read costs
# far LESS (2026-08-11, tailoring.drafting.score_job's fit_score prompt-
# caching fix: real measured data showed the 60,336-token profile+system
# prompt was ~99.6% of every fit_score call's input, repeated byte-for-byte
# on every call with no caching at all - see that commit for the real
# before/after numbers). Only the ephemeral/5-min tier is used anywhere in
# Panga today - update this if a 1-hour-TTL cache_control block is ever
# added (that tier has its own, higher write multiplier).
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.10


def estimate_response_cost(response, model: str) -> float:
    """Real dollar cost of one Anthropic API response: input + output
    tokens at the given model's per-token rate, plus $10/1,000 for each
    web_search the model actually ran (the server tool bills separately
    from tokens). Raises KeyError if `model` isn't in the pricing table -
    fail loudly rather than silently under-reporting cost for an unpriced
    model.

    Prices cache_creation_input_tokens/cache_read_input_tokens separately
    from plain input_tokens (2026-08-11 gap found while building fit_score
    prompt caching: before this, a cached call's real savings would have
    happened on Anthropic's side but this function would keep pricing
    every token at the full input rate, silently over-reporting cost and
    hiding the fix's real effect from cost_log). Anthropic's Python SDK
    Usage object omits these fields entirely on a response that used no
    caching at all, so both are read defensively via getattr."""
    pricing = _TOKEN_PRICING_PER_MTOK[model]
    cache_write_tokens = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
    cache_read_tokens = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    token_cost = (
        response.usage.input_tokens / 1_000_000 * pricing["input"]
        + cache_write_tokens / 1_000_000 * pricing["input"] * _CACHE_WRITE_MULTIPLIER
        + cache_read_tokens / 1_000_000 * pricing["input"] * _CACHE_READ_MULTIPLIER
        + response.usage.output_tokens / 1_000_000 * pricing["output"]
    )
    searches_run = sum(1 for b in response.content if b.type == "web_search_tool_result")
    return token_cost + searches_run * _WEB_SEARCH_PER_SEARCH
