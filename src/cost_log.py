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

Shared-across-worktrees path resolution (2026-08-13, real incident): every
other store under data/ (jobs.json, applications.json, ...) is deliberately
PER-CHECKOUT - PROJECT_ROOT used to be computed from `Path(__file__).
resolve().parents[1]`, i.e. wherever THIS copy of cost_log.py physically
lives, which is exactly right for those stores (each git worktree gets its
own isolated test data on purpose). cost_log.json is different: it is
llm_client._check_spend_cap()'s real dollar ledger for a real Anthropic
bill, and money spent from a worktree process is exactly as real as money
spent from the main checkout - there is only ever ONE actual $/day being
spent, no matter how many separate `data/` directories exist on disk (one
per worktree, since data/ is gitignored and each worktree checks out its
own copy of the tree). Using the file's-own-location PROJECT_ROOT here gave
every worktree its own independent, empty cost_log.json and therefore its
own independent $10/day budget - confirmed live 2026-08-13: the main
checkout showed $14.76 spent while a worktree simultaneously showed $1.75
in its own separate file, neither process aware of the other's spend, both
still willing to spend more.

_resolve_shared_data_dir() fixes this by always resolving to the MAIN
checkout's data/ directory, regardless of which worktree's copy of this
file is actually executing - a linked worktree's `.git` is a FILE (not a
directory) containing `gitdir: <main_repo>/.git/worktrees/<name>`, which is
enough to walk back to the one real main checkout on disk (see function
docstring). This is design option 1 from the fix's own ask (an absolute,
git-anchored path) rather than option 2 (a brand-new shared file under
.claude/, following message_board.py's convention) - kept in data/ with the
SAME crypto_store encryption/shape/tooling every existing reader (Ops tab,
last_cost_for_job, the whole cost_log test suite) already expects, instead
of introducing a second, unencrypted, differently-shaped copy of real
financial data living outside data/'s existing security convention for no
benefit other than avoiding a git-anchor computation.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from security.crypto_store import read_json, write_json
from security.file_lock import locked

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_shared_data_dir(start: Path | None = None) -> Path:
    """The one real, shared data/ directory cost_log.json (and its lock)
    must live under - always the MAIN checkout's, never a linked worktree's
    own copy. `start` is the checkout root to resolve FROM (defaults to
    PROJECT_ROOT, i.e. wherever this file itself actually lives) - exposed
    as a parameter so tests can point it at a fabricated worktree/main-repo
    pair on disk without needing a second real git checkout.

    PANGA_MAIN_DATA_DIR overrides everything below when set - an explicit
    escape hatch for an unusual layout (or a test) that doesn't want git-
    anchor detection at all, same "operator override always wins" pattern
    PANGA_DAILY_SPEND_CAP_USD/PANGA_KEYRING_SERVICE already use elsewhere
    in this codebase.

    Detection: a normal (non-worktree) checkout has `.git` as a real
    directory - nothing to resolve, `start` IS the main checkout, return its
    own data/. A linked worktree (created via `git worktree add`, the
    pattern every Panga feature branch uses under .claude/worktrees/) has
    `.git` as a plain text FILE whose single line is
    `gitdir: <main_repo>/.git/worktrees/<name>` - parsing that path and
    walking up 3 levels (<name> -> worktrees -> .git -> repo root) recovers
    the one real main checkout, confirmed against this exact repo's real
    worktree layout (git_marker.read_text() -> gitdir path -> parents[2]).
    Falls back to `start`'s own data/ (the old, worktree-local behavior)
    only if the `.git` file doesn't parse as expected or the recovered path
    doesn't actually check out as a real git repo - logged loudly (ERROR)
    since that means a worktree process silently reverted to its own
    isolated budget, exactly the bug this function exists to close."""
    override = os.environ.get("PANGA_MAIN_DATA_DIR")
    if override:
        return Path(override)

    root = start if start is not None else PROJECT_ROOT
    git_marker = root / ".git"

    if git_marker.is_dir():
        return root / "data"

    if git_marker.is_file():
        try:
            text = git_marker.read_text(encoding="utf-8").strip()
            prefix = "gitdir:"
            if text.startswith(prefix):
                gitdir = Path(text[len(prefix):].strip())
                if not gitdir.is_absolute():
                    gitdir = (root / gitdir).resolve()
                main_root = gitdir.parents[2]
                if (main_root / ".git").is_dir():
                    return main_root / "data"
        except (OSError, IndexError):
            pass

    logger.error(
        "Could not resolve the main checkout from %s's .git - falling back "
        "to this checkout's own data/ directory. If this is a linked git "
        "worktree, its cost_log.json/spend cap will be isolated from the "
        "real shared ledger until this is fixed (set PANGA_MAIN_DATA_DIR "
        "to override explicitly).",
        root,
    )
    return root / "data"


_SHARED_DATA_DIR = _resolve_shared_data_dir()
COST_LOG_PATH = _SHARED_DATA_DIR / "cost_log.json"
LOCK_DIR = _SHARED_DATA_DIR / ".locks"


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
    with locked("cost_log", lock_dir=LOCK_DIR):
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
