"""Real per-call API cost log (score-first-resume-flow spec, item 7).
api_cost.py's estimate_response_cost() already computes real dollar cost
from actual token usage, but until now it was only wired into
call_with_web_search() - the vast majority of real spend goes through
llm_client.call_structured() (every job score, resume/cover-letter/etc.
draft, keyword extraction), which computed nothing and logged nothing.

This module persists each call's real cost instead of letting it be
computed and discarded - one entry per call: timestamp, purpose, model,
token counts, and dollar cost, plus an optional job_key so a later
regenerate-confirmation prompt (item 6) can look up "what did the last
real generation for this specific job actually cost."

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
    job_key: tuple[str, str] | None = None,
) -> None:
    """Appends one real, already-computed call's cost to the log. job_key
    is (source, job_id) when this call was for a specific job posting
    (resume/cover-letter drafting, keyword extraction, fit scoring) - None
    for calls with no single job to attribute to."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "purpose": purpose,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }
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
