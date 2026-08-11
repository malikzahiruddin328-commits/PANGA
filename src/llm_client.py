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

2026-08-10 adversarial self-audit (real findings, not reassurance) fixed
four gaps in the above:
1. get_client() now passes max_retries=0 - the Anthropic SDK has its OWN
   built-in retry (default max_retries=2, i.e. 3 real HTTP attempts per
   call) that was still silently active underneath _call_with_retries.
   Empirically confirmed (mock transport, real request count): one
   "exhausted" call made 12 real HTTP requests, not the ~4 _MAX_ATTEMPTS
   implies, with the SDK's own backoff compounding on top of ours -
   directly undermining Tier 2's whole point (stop hammering an
   overloaded model). The SDK's status-code-only retry check also can't
   see the mid-stream .type-based failures this module already had to
   special-case, so disabling it loses nothing.
2. _call_with_retries now measures request_ms as the SUM of each real
   attempt's own duration, excluding the artificial time.sleep() backoff
   between attempts - the old wall-clock-since-call-started measurement
   counted backoff sleep as "latency," so any call needing 2+ retries
   would cross the Ops tab's 3.0s "slow call" flag from pure wait time
   alone, regardless of real model speed.
3. A call that exhausts retries and fallback without ever getting a
   response now gets its own cost_log entry (success=False, $0 cost,
   real duration/attempt_count/models_tried) via _log_failed_call -
   before this, only successful calls ever reached cost_log, so real
   failed-call volume was invisible to every cost/ops analysis, and
   would look like reduced traffic (not a problem) during a real outage.
4. The final failure's log line now includes attempt_count and
   models_tried, not just the last exception's own status/type/
   request_id - previously indistinguishable from a first-attempt-only
   failure when reading panga_debug.log after the fact.

Daily spend cap (2026-08-11): CLAUDE.md's cost-blast-radius rule stops
expensive new code from shipping unchecked, but nothing stopped an
already-shipped, already-running process (the daily job-search batch
scoring 455 jobs) from spending past a real limit once it started -
there was no runtime circuit breaker, only development-time discipline.
_check_spend_cap() closes that gap: called at the very top of every real
call (before any HTTP request is even prepared), it blocks NEW calls once
today's real cost_log spend reaches the cap, but never touches a call
already past this check - same "finish what's running, halt anything
new" pattern as tonight's C2 design discussion. Originally sized at $20
from real production cost_log data (2026-08-11): a normal light day
(2026-08-09) spent $2.77; the runaway fit_score batch that actually
happened 2026-08-11 crossed $10 within ~7 minutes of starting and
reached $63.86 before the day was half over - $20 was "meaningfully
better than the $63 incident," not Zahir's actual comfort number.
Lowered to DEFAULT_DAILY_SPEND_CAP_USD=10.0 the same day once he
confirmed what he's really comfortable spending day to day on AI calls
for a personal job-search tool - override via PANGA_DAILY_SPEND_CAP_USD
if that default proves wrong in practice (e.g. temporarily raising it
for a deliberate large batch run). Resets at UTC midnight (matches
every cost_log timestamp's own timezone - this codebase has no local-
timezone handling anywhere else, so introducing one here for calendar-day
alignment would add complexity for a benefit that doesn't matter: what
matters is bounding a day's spend, not precisely which midnight).

Known limitation, documented rather than silently assumed away: the cap
check reads cost_log fresh each call but isn't an atomic reservation - if
several calls are genuinely concurrent (the Streamlit app and a scheduled
task can both be mid-call at once), they can each pass the check before
any of them logs its own cost, so the cap can be overshot by the
concurrently-in-flight calls' combined cost before the block engages.
This is a circuit breaker (stop the bleeding soon after crossing), not a
hard ceiling - a precise version would need real distributed reservation
logic this codebase has no infrastructure for elsewhere, and would be
over-engineering for what's actually needed here.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_MODEL = "claude-sonnet-5"

DEFAULT_DAILY_SPEND_CAP_USD = 10.0  # see module docstring for the real-data reasoning
SPEND_CAP_ERROR_TYPE = "spend_cap_exceeded"  # cost_log entries blocked by the cap use this error_type

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
    it. attempt_count/models_tried (2026-08-10 audit finding #29) - without
    these, this log line was indistinguishable from a first-attempt-only
    failure, even when it actually followed a full retry+fallback
    exhaustion; a real incident read from panga_debug.log couldn't tell
    the two apart after the fact."""
    logger.error(
        "Claude API call failed after %s attempt(s), models tried=%s (status=%s type=%s request_id=%s): %s",
        getattr(exc, "_panga_attempt_count", 1), getattr(exc, "_panga_models_tried", None),
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


class LLMSpendCapExceeded(LLMCallFailed):
    """Raised by _check_spend_cap() when today's real cost_log spend has
    already reached the daily cap, BEFORE any HTTP request for this call
    is made (2026-08-11 - see module docstring's "Daily spend cap"
    section). Subclasses LLMCallFailed so every existing `except
    LLMCallFailed` still catches this the same way; a caller that wants
    to react specifically (e.g. stop a batch loop early rather than
    treating it as one failed item among many) can catch this narrower
    type instead."""
    pass


@dataclass
class _RetryResult:
    """What _call_with_retries actually did, not just what it returned -
    request_ms/attempt_count/models_tried let a caller log a genuinely
    honest record (real API time only, real attempt history) instead of
    just the response object."""
    response: object
    model: str
    request_ms: float
    attempt_count: int
    models_tried: list = field(default_factory=list)


def _attach_attempt_info(exc: BaseException, attempt_count: int, models_tried: list, request_ms: float) -> None:
    """Stamps the real attempt history onto an exhausted-failure exception
    before it's raised, so callers can log an honest failed-call record
    (_log_failed_call) and an honest log line (_clean_message_for_status_
    error) - both need to know this was attempt N of models [...], not
    just the last exception's own status/type. Setting arbitrary attributes
    on an exception instance is safe (Python doesn't restrict it) and
    avoids wrapping in a new exception type, which would break the
    `except anthropic.APIStatusError`/`except anthropic.APIConnectionError`
    catches every caller already has."""
    exc._panga_attempt_count = attempt_count
    exc._panga_models_tried = list(models_tried)
    exc._panga_request_ms = request_ms


def _call_with_retries(make_request, primary_model: str, on_retry=None) -> _RetryResult:
    """Runs make_request(model) against `primary_model`, retrying up to
    _MAX_ATTEMPTS total attempts (with exponential backoff) on transient
    errors only. If the primary model is still specifically overloaded once
    retries are exhausted, makes one further attempt against FALLBACK_MODEL
    instead of continuing to hammer the same overloaded model. Non-transient
    errors and a failed fallback attempt propagate to the caller unchanged -
    but first get the real attempt history stamped on via
    _attach_attempt_info, so even a failure carries honest diagnostics.

    request_ms sums each real attempt's own duration only - NOT the
    time.sleep() backoff between attempts, which is artificial wait, not
    real API latency (2026-08-10 audit finding: the old wall-clock
    measurement counted that sleep as "latency," inflating every retried
    call's reported duration regardless of real model speed).

    Returns a _RetryResult on success.
    """
    last_exc = None
    request_ms = 0.0
    models_tried = []
    attempt_count = 0
    for attempt in range(_MAX_ATTEMPTS):
        attempt_count += 1
        models_tried.append(primary_model)
        started_at = time.perf_counter()
        try:
            response = make_request(primary_model)
        except anthropic.AnthropicError as exc:
            request_ms += (time.perf_counter() - started_at) * 1000
            if not _is_transient(exc):
                _attach_attempt_info(exc, attempt_count, models_tried, request_ms)
                raise
            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1:
                if on_retry:
                    on_retry(attempt + 1)
                time.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
            continue
        request_ms += (time.perf_counter() - started_at) * 1000
        return _RetryResult(response, primary_model, request_ms, attempt_count, models_tried)

    if _is_overloaded(last_exc) and FALLBACK_MODEL != primary_model:
        attempt_count += 1
        models_tried.append(FALLBACK_MODEL)
        started_at = time.perf_counter()
        try:
            response = make_request(FALLBACK_MODEL)
        except anthropic.AnthropicError as exc:
            request_ms += (time.perf_counter() - started_at) * 1000
            _attach_attempt_info(exc, attempt_count, models_tried, request_ms)
            raise
        request_ms += (time.perf_counter() - started_at) * 1000
        return _RetryResult(response, FALLBACK_MODEL, request_ms, attempt_count, models_tried)

    _attach_attempt_info(last_exc, attempt_count, models_tried, request_ms)
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
    # max_retries=0 (2026-08-10 audit finding #27): the Anthropic SDK's own
    # default retry (max_retries=2, i.e. 3 real HTTP attempts per call) was
    # otherwise still active underneath _call_with_retries, stacking with
    # it - empirically confirmed to turn one "exhausted" call into 12 real
    # HTTP requests, not the ~4 _MAX_ATTEMPTS implies. _call_with_retries
    # already owns retry/backoff/fallback policy end-to-end, including the
    # mid-stream .type-based detection the SDK's own status-code-only
    # check can't do - the SDK's hidden retry is redundant at best here,
    # counterproductive (extra hammering of an already-overloaded model)
    # at worst.
    return anthropic.Anthropic(max_retries=0)


def _daily_spend_cap_usd() -> float:
    """Read fresh per call (not cached at import time) so an operator can
    raise/lower PANGA_DAILY_SPEND_CAP_USD without restarting the app -
    same per-call-env-read convention call_structured already uses for
    ANTHROPIC_MODEL. Falls back to the default rather than raising on a
    malformed value - a bad env var must never itself become an outage on
    every single AI call."""
    raw = os.environ.get("PANGA_DAILY_SPEND_CAP_USD")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.error("PANGA_DAILY_SPEND_CAP_USD=%r isn't a valid number - using the default $%.2f instead.", raw, DEFAULT_DAILY_SPEND_CAP_USD)
    return DEFAULT_DAILY_SPEND_CAP_USD


def _todays_spend_usd() -> float:
    """Real cumulative cost_log spend for the current UTC calendar day.
    Reads cost_log fresh every call - deliberately not cached, since the
    whole point is real-time enforcement, and cost_log is a small,
    infrequently-read file at this call frequency (once per real AI call,
    not a hot per-event path)."""
    from cost_log import load_cost_log

    today = datetime.now(timezone.utc).date().isoformat()
    return sum(
        e.get("cost_usd") or 0
        for e in load_cost_log()
        if (e.get("timestamp") or "").startswith(today)
    )


_cap_alarm_logged_for_date = None  # tracks which UTC date already got its one CRITICAL log line


def _log_cap_block(purpose: str) -> None:
    """Records a blocked (never-attempted) call to cost_log in the same
    success=False shape _log_failed_call uses for genuine API failures
    (error_type="spend_cap_exceeded" distinguishes the two) - so the Ops
    tab's failed_call_count surfaces a cap trip the same way it surfaces
    a real failure, not a second invisible bucket nobody thinks to check.
    Swallows its own failures - a logging problem must never mask the
    real LLMSpendCapExceeded already being raised to the caller."""
    try:
        from cost_log import log_api_cost

        log_api_cost(
            purpose=purpose, model="none", input_tokens=0, output_tokens=0, cost_usd=0.0,
            success=False, error_type=SPEND_CAP_ERROR_TYPE, attempt_count=0, models_tried=[],
        )
    except Exception:
        logger.exception("Failed to log spend-cap-block record (purpose=%s).", purpose)


def spend_cap_tripped_today() -> bool:
    """True if the daily spend cap blocked any call today (UTC), in ANY
    process - reads cost_log directly rather than in-process state, since
    the Streamlit app and up to 3 scheduled tasks all share one cost_log
    and any of them could have been the one that tripped it. Lets a
    caller like scripts/run_search.py's daily scheduled task tell the
    difference between "nothing to report" and "the spend cap silently
    blocked most of today's scoring" in its own summary notification -
    2026-08-11 fast-follow: the notification Zahir already gets from that
    task should say this, not require him to separately check the Ops
    tab or panga_debug.log to find out why a batch came up short."""
    from cost_log import load_cost_log

    today = datetime.now(timezone.utc).date().isoformat()
    return any(
        e.get("error_type") == SPEND_CAP_ERROR_TYPE and (e.get("timestamp") or "").startswith(today)
        for e in load_cost_log()
    )


def _check_spend_cap(purpose: str) -> None:
    """Raises LLMSpendCapExceeded if today's real spend has already
    reached the daily cap - called at the very top of call_structured/
    call_with_web_search, before any HTTP request is even built, so a
    tripped cap blocks NEW calls only. Anything that already passed this
    check keeps running to completion, including its own retries/
    fallback attempt inside _call_with_retries - there's no cap check
    inside that loop, by design, matching "finish what's running, halt
    anything new" (see module docstring).

    The FIRST block of a given UTC day logs at CRITICAL - always written
    to panga_debug.log regardless of PANGA_DEBUG (debug_log.setup_always_
    on_error_logging's handler is set at ERROR level, and CRITICAL > ERROR)
    - so this is genuinely unmissable, not a line buried among routine
    per-call ERROR logs. Every subsequent block that same day logs at
    ERROR instead, so a batch loop hammering into the cap doesn't spam
    CRITICAL alarms for every single blocked item. The "once per day"
    dedup is per-process (a plain module global) - Panga runs several
    processes (the Streamlit app, up to 3 scheduled tasks), each with its
    own separate copy of this state, so if two different processes both
    independently hit the cap the same day, each logs its own one-time
    CRITICAL rather than there being a single system-wide alarm. Still far
    better than one CRITICAL per blocked call, and correct - a real
    cross-process dedup would need shared state this codebase doesn't
    have infrastructure for elsewhere, and isn't worth building for a
    log-spam concern this minor."""
    spent = _todays_spend_usd()
    cap = _daily_spend_cap_usd()
    if spent < cap:
        return

    global _cap_alarm_logged_for_date
    today = datetime.now(timezone.utc).date().isoformat()
    if _cap_alarm_logged_for_date != today:
        _cap_alarm_logged_for_date = today
        logger.critical(
            "DAILY SPEND CAP EXCEEDED - blocking all further AI calls until it resets at UTC midnight. "
            "Spent $%.2f today, cap is $%.2f. First blocked purpose: %s.",
            spent, cap, purpose,
        )
    else:
        logger.error("Blocked AI call - daily spend cap still exceeded ($%.2f spent, cap $%.2f, purpose=%s).", spent, cap, purpose)

    _log_cap_block(purpose)
    raise LLMSpendCapExceeded(
        f"Today's AI spend (${spent:.2f}) has reached the daily cap (${cap:.2f}). "
        "New AI calls are paused until it resets at UTC midnight. Anything already running will finish."
    )


def _log_cost(
    response, model: str, purpose: str, job_key: tuple[str, str] | None,
    duration_ms: float | None = None,
) -> None:
    """Logs one call's real cost (score-first-resume-flow spec item 7) -
    swallows its own failures so a logging problem never breaks the actual
    API call it's just trying to record. Runs even on a refused/truncated
    response, since real tokens were still billed either way. duration_ms
    is _call_with_retries' request_ms (2026-08-10 audit fix) - the SUM of
    each real attempt's own duration, excluding backoff-sleep wait between
    attempts, passed through by the caller from its _RetryResult."""
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


def _log_failed_call(exc: BaseException, purpose: str, job_key: tuple[str, str] | None) -> None:
    """Logs a call that never got a response - retries and model fallback
    (if applicable) exhausted, or a non-transient error on the first
    attempt (2026-08-10, audit finding #26). Before this, _log_cost only
    ran on success, so a failed call's real attempt volume and elapsed
    time were completely invisible to cost_log/the Ops tab - during an
    actual outage the dashboard would show reduced call volume, not the
    failures actually happening. cost_usd/token counts are 0: a genuine
    pre-generation API error (the only kind that reaches here) has no
    billed generation. Swallows its own failures same as _log_cost - a
    logging problem must never mask or replace the real error already in
    flight to the caller."""
    try:
        from cost_log import log_api_cost

        models_tried = getattr(exc, "_panga_models_tried", None) or ["unknown"]
        log_api_cost(
            purpose=purpose, model=models_tried[-1], input_tokens=0, output_tokens=0, cost_usd=0.0,
            job_key=job_key, duration_ms=getattr(exc, "_panga_request_ms", None),
            success=False, error_type=_error_type(exc) or type(exc).__name__,
            attempt_count=getattr(exc, "_panga_attempt_count", 1), models_tried=models_tried,
        )
    except Exception:
        logger.exception("Failed to log failed-call record (purpose=%s) - the original error still propagates.", purpose)


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
    _check_spend_cap(purpose)

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

    try:
        result = _call_with_retries(make_request, primary_model, on_retry=report_retry)
    except anthropic.APIStatusError as exc:
        _log_failed_call(exc, purpose, job_key)
        raise LLMCallFailed(_clean_message_for_status_error(exc)) from exc
    except anthropic.APIConnectionError as exc:
        logger.error(
            "Claude API connection error after %s attempt(s), models tried=%s (purpose=%s): %s",
            getattr(exc, "_panga_attempt_count", 1), getattr(exc, "_panga_models_tried", None), purpose, exc,
        )
        _log_failed_call(exc, purpose, job_key)
        raise LLMCallFailed("Couldn't reach the Claude API - check your internet connection.") from exc
    response, call_model = result.response, result.model

    _log_cost(response, call_model, purpose, job_key, duration_ms=result.request_ms)

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
    The daily spend cap (2026-08-11) respects this same contract: a
    tripped cap returns ("", 0.0) same as any other blocked call here,
    rather than raising LLMSpendCapExceeded the way call_structured does -
    this function's existing callers have never had a reason to wrap it
    in a try/except, and breaking that now would risk an unhandled crash
    somewhere that's never needed one before.

    purpose/job_key (score-first-resume-flow spec item 7): same real-cost
    logging as call_structured() - see _log_cost()."""
    from api_cost import estimate_response_cost
    from debug_log import setup_always_on_error_logging

    setup_always_on_error_logging()
    try:
        _check_spend_cap(purpose)
    except LLMSpendCapExceeded:
        return "", 0.0

    primary_model = model or DEFAULT_MODEL

    def make_request(call_model):
        return client.messages.create(
            model=call_model,
            max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )

    try:
        result = _call_with_retries(make_request, primary_model)
    except anthropic.APIStatusError as exc:
        logger.error(
            "Claude web-search call failed after %s attempt(s), models tried=%s (purpose=%s status=%s type=%s request_id=%s): %s",
            getattr(exc, "_panga_attempt_count", 1), getattr(exc, "_panga_models_tried", None),
            purpose, exc.status_code, _error_type(exc), exc.request_id, exc.message,
        )
        _log_failed_call(exc, purpose, job_key)
        return "", 0.0
    except anthropic.APIConnectionError as exc:
        logger.error(
            "Claude web-search connection error after %s attempt(s), models tried=%s (purpose=%s): %s",
            getattr(exc, "_panga_attempt_count", 1), getattr(exc, "_panga_models_tried", None), purpose, exc,
        )
        _log_failed_call(exc, purpose, job_key)
        return "", 0.0
    response, call_model = result.response, result.model

    cost = estimate_response_cost(response, call_model)
    _log_cost(response, call_model, purpose, job_key, duration_ms=result.request_ms)
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text, cost
