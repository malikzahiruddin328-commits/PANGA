"""Real per-call API cost log (score-first-resume-flow spec, item 7).
api_cost.py's estimate_response_cost() already computes real dollar cost
from actual token usage, but until now it was only wired into
call_with_web_search() - the vast majority of real spend goes through
llm_client.call_structured() (every job score, resume/cover-letter/etc.
draft, keyword extraction), which computed nothing and logged nothing.

This module persists each call's real cost instead of letting it be
computed and discarded - one entry per call: timestamp, purpose, model,
token counts, dollar cost, and (2026-08-10, Ops tab) real call duration,
plus an optional job_key so a later regenerate-confirmation prompt (item
6) can look up "what did the last real generation for this specific job
actually cost."

Encrypted at rest via security.crypto_store, same as every other store
under data/ - not a cost-dashboard UI (explicitly out of scope), just a
real number for other code to read.
"""

from datetime import datetime, timezone
from pathlib import Path

from security.crypto_store import read_json, write_json
from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COST_LOG_PATH = PROJECT_ROOT / "data" / "cost_log.json"


def log_api_cost(
    purpose: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float,
    job_key: tuple[str, str] | None = None, duration_ms: float | None = None,
    success: bool = True, error_type: str | None = None,
    attempt_count: int | None = None, models_tried: list[str] | None = None,
    cache_creation_input_tokens: int | None = None, cache_read_input_tokens: int | None = None,
) -> None:
    """Appends one real, already-computed call's cost to the log. job_key
    is (source, job_id) when this call was for a specific job posting
    (resume/cover-letter drafting, keyword extraction, fit scoring) - None
    for calls with no single job to attribute to. duration_ms is the real
    SUM of per-attempt request time the caller measured around
    llm_client._call_with_retries() - every attempt's own real API time,
    added together, but NOT the artificial backoff-sleep wait between
    attempts (2026-08-10 audit finding: including sleep time inflated
    every retried call's latency past the Ops tab's 3.0s "slow call"
    threshold regardless of how fast the model itself actually responded -
    see llm_client._RetryResult). Optional/None for any caller that
    doesn't measure it.

    success/error_type/attempt_count/models_tried (2026-08-10 audit
    finding #26): before this, only calls that eventually succeeded ever
    reached this function - a call that exhausted retries and model
    fallback without ever getting a response was silently invisible to
    every cost/ops analysis, even though it consumed real attempts and
    real wall-clock time. success=False records exactly that case -
    cost_usd/token counts are 0 (no generation was billed for a genuine
    pre-generation API error, the only kind that reaches here), but
    duration_ms/error_type/attempt_count/models_tried preserve what
    actually happened. success defaults True so every existing caller
    (which never had a reason to think about failure logging) keeps
    working unchanged.

    cache_creation_input_tokens/cache_read_input_tokens (2026-08-11,
    fit_score prompt-caching fix): the real per-call breakdown of Anthropic
    prompt-caching token pools, straight from the response, so a cache hit
    is independently verifiable in this log later - not just inferred from
    a lower cost_usd. None (the default) for any call that didn't use
    caching at all, same "optional, existing callers unaffected" pattern
    as every other optional field here."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "purpose": purpose,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "success": success,
    }
    if cache_creation_input_tokens:
        entry["cache_creation_input_tokens"] = cache_creation_input_tokens
    if cache_read_input_tokens:
        entry["cache_read_input_tokens"] = cache_read_input_tokens
    if not success:
        entry["error_type"] = error_type
        entry["attempt_count"] = attempt_count
        entry["models_tried"] = models_tried
    if job_key:
        entry["source"], entry["job_id"] = job_key
    with locked("cost_log"):
        entries = read_json(COST_LOG_PATH, default=[])
        entries.append(entry)
        write_json(COST_LOG_PATH, entries)


def load_cost_log() -> list[dict]:
    return read_json(COST_LOG_PATH, default=[])


def last_cost_for_job(source: str, job_id: str, purpose: str | None = None) -> float | None:
    """The real logged cost of the most recent call for this specific job
    (optionally narrowed to one purpose, e.g. "resume_draft") - None if
    nothing's been logged for it yet (e.g. it predates this feature)."""
    matches = [
        e for e in load_cost_log()
        if e.get("source") == source and e.get("job_id") == job_id
        and (purpose is None or e.get("purpose") == purpose)
    ]
    if not matches:
        return None
    return max(matches, key=lambda e: e["timestamp"])["cost_usd"]
