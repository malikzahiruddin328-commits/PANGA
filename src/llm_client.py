"""Shared direct-Anthropic-API plumbing (native-packaging branch,
2026-07-31). Every direct-API module in Panga - tailoring/drafting.py,
prospector/prospector_score.py, prospector/company_lookup.py, and the
standalone-script reasoning this branch is adding (fit scoring, CTA
classification/drafting, interview prep, LinkedIn enhance, rejection
diagnosis, learn engine) - independently reimplemented client setup, the
streamed-structured-output call pattern, and the same API-error handling.
This module is the one place that plumbing lives now. The original modules
keep their old public names (DraftingNotConfigured, DraftingFailed,
DEFAULT_MODEL, _client) as re-exports so no existing import elsewhere in
the app needs to change.

Reliability (2026-08-05): every call goes through _call_with_retries, which
gives two layers of protection against Claude-side transient failures -
(1) bounded retry-with-backoff for overloaded/rate-limit/connection errors,
(2) a single fallback attempt on a different Claude model if the primary is
still specifically overloaded after retries. A third layer (a second AI
provider or a local model) was deliberately deferred: everything observed
in practice has been isolated single-request blips, not sustained outages,
and at Claude's normal uptime that residual risk doesn't justify the extra
engineering and quality compromise a non-Claude fallback would mean. Revisit
only if a real sustained outage is actually observed - don't rebuild this
analysis from scratch first.
"""

import json
import logging
import os
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_MODEL = "claude-sonnet-5"

# Transient errors worth retrying/falling back on. Anything else (bad
# request, auth, content policy, etc.) propagates on the first attempt.
# Per anthropic._client._make_status_error, 5xx responses other than 529
# (overloaded) all surface as the generic InternalServerError - there's no
# separate ServiceUnavailableError/DeadlineExceededError raised in practice.
#
# Errors that arrive mid-stream (call_structured's normal path) are a
# separate case: the HTTP response already came back 200 before the error
# event, so the SDK can only classify by status code and falls through to
# a bare APIStatusError - confirmed live 2026-08-05 hitting a real
# overloaded_error this way. _error_type() below reads exc.type (which the
# SDK already parsed from the error body) so those aren't missed.
_TRANSIENT_ERROR_TYPES = frozenset(
    {"overloaded_error", "rate_limit_error", "api_error", "timeout_error"}
)

_MAX_ATTEMPTS = 3  # bounded - CLAUDE.md's no-unconditional-loops rule
_BACKOFF_BASE_SECONDS = 1


def _error_type(exc: BaseException) -> str | None:
    """The API's own error-type string (e.g. "overloaded_error"). APIStatusError
    already parses this onto `.type` from the response body in its __init__,
    regardless of which specific subclass got raised - see
    _TRANSIENT_ERROR_TYPES above for why the subclass alone isn't reliable."""
    return getattr(exc, "type", None)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, (anthropic.RateLimitError, anthropic.OverloadedError, anthropic.InternalServerError)):
        return True
    return isinstance(exc, anthropic.APIStatusError) and _error_type(exc) in _TRANSIENT_ERROR_TYPES


def _is_overloaded(exc: BaseException) -> bool:
    return isinstance(exc, anthropic.OverloadedError) or _error_type(exc) == "overloaded_error"


def _is_billing_error(exc: anthropic.APIStatusError) -> bool:
    """Real case Zahir hit live 2026-08-09: a 400 invalid_request_error
    with the message "Your credit balance is too low to access the
    Anthropic API." General only found this by bypassing the app and
    calling the API directly with a raw script - the generic catch-all
    message gave no way to tell a billing problem apart from a rate
    limit, an outage, or a real bug. A distinct, identifiable error
    shape (not a guess): status 400, type invalid_request_error, and
    Anthropic's own fixed message text for this specific case."""
    return exc.status_code == 400 and _error_type(exc) == "invalid_request_error" and "credit balance" in exc.message.lower()


def _clean_message_for_status_error(exc: anthropic.APIStatusError) -> str:
    """Human-readable message for an error that's reached the end of the
    line - retries and model fallback (if applicable) are already
    exhausted. The full technical detail (status code, error type,
    request_id) is logged via the standard `logging` module - always
    written to data/logs/panga_debug.log regardless of PANGA_DEBUG for
    exactly this class of failure (see debug_log.setup_always_on_error_
    logging(), 2026-08-09 - a real llm_client call failure is significant
    enough that Zahir shouldn't need debug mode on to have it captured) -
    but never put in the exception message itself, since that string is
    what app.py's `st.error(str(exc))` shows verbatim to the end user.
    Zahir hit the raw JSON dump live 2026-08-05 and flagged it directly:
    a real user has no use for a JSON error blob and no way to act on
    it."""
    logger.error(
        "Claude API call failed (status=%s type=%s request_id=%s): %s",
        exc.status_code, _error_type(exc), exc.request_id, exc.message,
    )
    if _is_billing_error(exc):
        return "Your Anthropic account is out of credits - add credits at console.anthropic.com/settings/billing, then try again."
    if _is_transient(exc):
        return "Claude's servers are temporarily busy - please try again in a moment."
    if exc.status_code in (401, 403):
        return "Claude API access problem - check your API key configuration."
    return "Something went wrong talking to Claude. Please try again."


class LLMNotConfigured(Exception):
    pass


class LLMCallFailed(Exception):
    pass


class LLMResponseTruncated(LLMCallFailed):
    """Raised specifically when stop_reason == "max_tokens" - a subclass of
    LLMCallFailed (not a separate hierarchy) so every existing `except
    LLMCallFailed` elsewhere keeps catching this the same way it always
    did. Exists so a caller that CAN meaningfully react to truncation
    specifically (e.g. retry the same call with a higher max_tokens) can
    catch just this, instead of the generic LLMCallFailed a refusal or
    invalid-JSON response also raises - those aren't fixed by a bigger
    token budget, so conflating them would make a blind retry-on-any-
    failure just as likely to retry something a bigger budget can't help.
    Added 2026-08-08 for tailoring.job_alert_reasoning.extract_listings'
    escalating-max_tokens retry - see that module for why."""
    pass


def _call_with_retries(make_request, primary_model: str, on_retry=None):
    """Runs make_request(model) against `primary_model`, retrying up to
    _MAX_ATTEMPTS total attempts (with exponential backoff) on transient
    errors only. If the primary model is still specifically overloaded once
    retries are exhausted, makes one further attempt against FALLBACK_MODEL
    instead of continuing to hammer the same overloaded model. Non-transient
    errors and a failed fallback attempt propagate to the caller unchanged.

    Returns (response, model_actually_used).
    """
    last_exc = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return make_request(primary_model), primary_model
        except anthropic.AnthropicError as exc:
            if not _is_transient(exc):
                raise
            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1:
                if on_retry:
                    on_retry(attempt + 1)
                time.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))

    if _is_overloaded(last_exc) and FALLBACK_MODEL != primary_model:
        return make_request(FALLBACK_MODEL), FALLBACK_MODEL
    raise last_exc


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def get_client() -> "anthropic.Anthropic":
    if not is_configured():
        raise LLMNotConfigured(
            "ANTHROPIC_API_KEY must be set in .env for direct-API calls. "
            "Get a key at console.anthropic.com, then add it to the .env "
            "file in the Panga folder (copy the same line USAJOBS_API_KEY "
            "uses) and restart the app."
        )
    return anthropic.Anthropic()


def _log_cost(
    response, model: str, purpose: str, job_key: tuple[str, str] | None,
    duration_ms: float | None = None,
) -> None:
    """Logs one call's real cost (score-first-resume-flow spec item 7) -
    swallows its own failures so a logging problem never breaks the actual
    API call it's just trying to record. Runs even on a refused/truncated
    response, since real tokens were still billed either way. duration_ms
    (Ops tab, 2026-08-10) is the caller's own measured wall-clock time
    around its _call_with_retries() call - measured by the caller, not
    here, since only the caller knows exactly when its own attempt(s)
    started."""
    try:
        from api_cost import estimate_response_cost
        from cost_log import log_api_cost

        cost = estimate_response_cost(response, model)
        log_api_cost(
            purpose=purpose, model=model,
            input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
            cost_usd=cost, job_key=job_key, duration_ms=duration_ms,
        )
    except Exception:
        logger.exception("Failed to log API call cost (purpose=%s) - the call itself still succeeded.", purpose)


def call_structured(
    client: "anthropic.Anthropic",
    system,
    user_content,
    schema: dict,
    max_tokens: int,
    model: str | None = None,
    effort: str = "high",
    thinking: bool = True,
    on_progress=None,
    refusal_message: str = "Claude declined to respond. Try again.",
    purpose: str = "unspecified",
    job_key: tuple[str, str] | None = None,
) -> dict:
    """One streamed call constrained to `schema` via structured output.
    Reports "thinking..."/"writing... (N characters so far)" through
    on_progress(substatus) as the response streams - same real-progress
    mechanism every direct-API module in Panga already implemented
    separately (Zahir's ask 2026-07-31: no spinner should be opaque when
    the underlying call can report real progress instead). Callers that
    need per-item progress framing (e.g. "drafting doc 2 of 5") wrap
    on_progress themselves before passing it in - this helper only knows
    about a single current-call substatus string.

    Returns the parsed JSON dict. Raises LLMNotConfigured (via the caller's
    own get_client() call, before this function is even reached) and
    LLMCallFailed on API error, refusal, truncation, or invalid JSON.

    Transient failures (overloaded/rate-limit/connection errors) are retried
    with backoff, then retried once more against a fallback model if the
    primary is still overloaded - see _call_with_retries.

    purpose/job_key (score-first-resume-flow spec item 7, 2026-08-08): every
    real call's cost is logged via cost_log.log_api_cost() once the call
    succeeds - api_cost.estimate_response_cost() already computed this from
    real usage for call_with_web_search(), it just wasn't being persisted
    anywhere for this function, which is the vast majority of real spend
    (every job score, document draft, keyword extraction). purpose defaults
    to "unspecified" so existing callers keep working unlabeled until
    updated; job_key (source, job_id) lets a later regenerate-confirmation
    prompt look up "what did the last real generation for this job cost."
    Logging failure never breaks the actual call - a cost-log write error
    is logged and swallowed, not raised, since it isn't the caller's job."""
    from debug_log import setup_always_on_error_logging

    setup_always_on_error_logging()

    primary_model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL

    def make_request(call_model):
        kwargs = dict(
            model=call_model,
            max_tokens=max_tokens,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        if thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        with client.messages.stream(**kwargs) as stream:
            char_count = 0
            last_reported = 0
            for event in stream:
                if event.type == "content_block_start" and event.content_block.type == "thinking":
                    if on_progress:
                        on_progress("thinking...")
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    char_count += len(event.delta.text)
                    if on_progress and char_count - last_reported >= 150:
                        on_progress(f"writing... ({char_count:,} characters so far)")
                        last_reported = char_count
            return stream.get_final_message()

    def report_retry(attempt_number):
        if on_progress:
            on_progress("Claude is busy, retrying...")

    call_started_at = time.perf_counter()
    try:
        response, call_model = _call_with_retries(make_request, primary_model, on_retry=report_retry)
    except anthropic.APIStatusError as exc:
        raise LLMCallFailed(_clean_message_for_status_error(exc)) from exc
    except anthropic.APIConnectionError as exc:
        logger.error("Claude API connection error (purpose=%s): %s", purpose, exc)
        raise LLMCallFailed("Couldn't reach the Claude API - check your internet connection.") from exc
    duration_ms = (time.perf_counter() - call_started_at) * 1000

    _log_cost(response, call_model, purpose, job_key, duration_ms=duration_ms)

    if response.stop_reason == "refusal":
        logger.error("Claude refused to respond (purpose=%s): %s", purpose, refusal_message)
        raise LLMCallFailed(refusal_message)
    if response.stop_reason == "max_tokens":
        logger.error("Claude response truncated at max_tokens=%s (purpose=%s)", max_tokens, purpose)
        raise LLMResponseTruncated("The response was cut off before finishing. Try again.")

    text_block = next((b.text for b in response.content if b.type == "text"), None)
    if not text_block:
        logger.error("Claude returned no text content block (purpose=%s)", purpose)
        raise LLMCallFailed("Claude returned no result.")
    try:
        return json.loads(text_block)
    except json.JSONDecodeError as exc:
        logger.error("Claude returned invalid JSON (purpose=%s): %s | raw text: %r", purpose, exc, text_block)
        raise LLMCallFailed("Claude's response wasn't valid - try again.") from exc


def call_with_web_search(
    client: "anthropic.Anthropic",
    system: str,
    user_content: str,
    max_tokens: int,
    max_uses: int = 5,
    model: str | None = None,
    purpose: str = "unspecified",
    job_key: tuple[str, str] | None = None,
) -> tuple[str, float]:
    """One-shot (non-streamed) call with the server-side web_search tool -
    the pattern tailoring/drafting.py's _lookup_company_address() and
    prospector/company_lookup.py's lookup_company_website() both
    implemented separately. Returns (response_text, cost_usd), computed
    from the real response usage via api_cost.estimate_response_cost - cost
    is 0.0 if the call failed before any usage was billed. Never raises on
    API error (both original callers treated a failed lookup as "no
    result found", not a hard error) - callers check for an empty string.

    purpose/job_key (score-first-resume-flow spec item 7): same real-cost
    logging as call_structured() - see _log_cost()."""
    from api_cost import estimate_response_cost
    from debug_log import setup_always_on_error_logging

    setup_always_on_error_logging()

    primary_model = model or DEFAULT_MODEL

    def make_request(call_model):
        return client.messages.create(
            model=call_model,
            max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )

    call_started_at = time.perf_counter()
    try:
        response, call_model = _call_with_retries(make_request, primary_model)
    except anthropic.APIStatusError as exc:
        logger.error(
            "Claude web-search call failed (purpose=%s status=%s type=%s request_id=%s): %s",
            purpose, exc.status_code, _error_type(exc), exc.request_id, exc.message,
        )
        return "", 0.0
    except anthropic.APIConnectionError as exc:
        logger.error("Claude web-search connection error (purpose=%s): %s", purpose, exc)
        return "", 0.0
    duration_ms = (time.perf_counter() - call_started_at) * 1000

    cost = estimate_response_cost(response, call_model)
    _log_cost(response, call_model, purpose, job_key, duration_ms=duration_ms)
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text, cost
