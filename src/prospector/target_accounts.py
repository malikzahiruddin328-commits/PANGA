"""Local JSON-backed store for the `target_accounts` table (PRD §16a): one
record per company worth watching before it's posted a role, parallel to
`jobs`. Encrypted at rest (PRD §7) via security.crypto_store.

Qualification rule (v0, deliberately crude - see §16a): a company reaches
"qualified" once it has 2+ DISTINCT signal types (e.g. a late-stage trial
AND a regulatory filing), not just 2 instances of the same type (two Phase
3 trials alone is still just one kind of evidence). Below that, it's
"watching". Manual states ("contacted", "stale", "disqualified") are
sticky - a new signal arriving later never silently overwrites a status
Zahir set himself; that's his call, not automation's. Every status change
(manual or automatic) bumps status_updated_at (added 2026-08-10, PRD S16a
#20) - records whose status hasn't changed since before this field
existed simply don't have it yet, same "not backfilled, not guessed at"
convention as job_store's date_added/applications.py's created_at.

Every read-modify-write function below runs inside security.file_lock.
locked() (found missing 2026-08-09 - same audit lens as the outreach.json
fix, commit 57d94ee, just not caught in the first pass since it's a
different file). Real concurrent-writer exposure here: add_signal() gets
called from live Claude Code reasoning sessions populating signals from
ClinicalTrials.gov/openFDA/etc. while the Streamlit dashboard - which
Zahir routinely has open at the same time - can call set_status()/
set_website() from his own clicks. TARGET_ACCOUNTS_PATH writes use the
"target_accounts" lock name; WEBSITE_LOOKUP_COST_PATH writes (a genuinely
separate file) get their own "website_lookup_cost" lock name rather than
sharing one - see security/file_lock.py's docstring on why each store
gets its own lock file.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from security.crypto_store import read_json, write_json
from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TARGET_ACCOUNTS_PATH = PROJECT_ROOT / "data" / "target_accounts" / "target_accounts.json"
WEBSITE_LOOKUP_COST_PATH = PROJECT_ROOT / "data" / "target_accounts" / "website_lookup_cost.json"

MANUAL_STATUSES = ("contacted", "stale", "disqualified")


def load_target_accounts() -> list[dict]:
    return read_json(TARGET_ACCOUNTS_PATH, default=[])


def _save_all(accounts: list[dict]) -> None:
    write_json(TARGET_ACCOUNTS_PATH, accounts)


def get_target_account(company_name: str) -> dict | None:
    for acc in load_target_accounts():
        if acc["company_name"].lower() == company_name.lower():
            return acc
    return None


def _recompute_status(account: dict) -> None:
    if account["status"] in MANUAL_STATUSES:
        return  # sticky - never auto-overwrite a status Zahir set himself
    distinct_types = {s["signal_type"] for s in account["signals"]}
    new_status = "qualified" if len(distinct_types) >= 2 else "watching"
    if account.get("status") != new_status:
        account["status_updated_at"] = datetime.now(timezone.utc).isoformat()
    account["status"] = new_status


def add_signal(
    company_name: str,
    signal_type: str,
    source: str,
    detail: str,
    date_observed: str | None = None,
    ref: str | None = None,
    industry: str | None = None,
) -> None:
    """Creates the target account if it doesn't exist yet, then appends the
    signal (deduped by (signal_type, source, ref) when ref is given - e.g.
    an NCT ID - else by (signal_type, source, detail)) and recomputes
    status. date_observed defaults to now if not given."""
    date_observed = date_observed or datetime.now(timezone.utc).isoformat()
    with locked("target_accounts"):
        accounts = load_target_accounts()

        account = next((a for a in accounts if a["company_name"].lower() == company_name.lower()), None)
        if account is None:
            account = {
                "company_name": company_name,
                "industry": industry,
                "status": "watching",
                "status_updated_at": datetime.now(timezone.utc).isoformat(),
                "signals": [],
                "notes": None,
            }
            accounts.append(account)

        dedupe_key = (signal_type, source, ref) if ref else (signal_type, source, detail)
        already_present = any(
            (s["signal_type"], s["source"], s.get("ref") or s["detail"]) == dedupe_key
            for s in account["signals"]
        )
        if not already_present:
            account["signals"].append({
                "signal_type": signal_type,
                "source": source,
                "detail": detail,
                "date_observed": date_observed,
                "ref": ref,
            })
            _recompute_status(account)

        _save_all(accounts)


def set_status(company_name: str, status: str, notes: str | None = None) -> None:
    """Manual override - Zahir marking a company contacted/stale/disqualified
    himself (or reverting one back to watching/qualified). status_updated_at
    (added 2026-08-10, PRD S16a #20) bumps only on a real status change,
    same rule as applications.py's status_updated_at - this is the "did
    Zahir already know about X when he made this call" reference point
    find_paused_accounts_with_new_activity() needs to tell a signal/posting
    that existed BEFORE the decision from one that arrived genuinely after
    it. Records whose status hasn't changed since before this field existed
    won't have it - excluded from timestamp-dependent checks, not guessed
    at, same convention as every other "added on date X" field in Panga."""
    with locked("target_accounts"):
        accounts = load_target_accounts()
        for acc in accounts:
            if acc["company_name"].lower() == company_name.lower():
                if acc.get("status") != status:
                    acc["status_updated_at"] = datetime.now(timezone.utc).isoformat()
                acc["status"] = status
                if notes is not None:
                    acc["notes"] = notes
                _save_all(accounts)
                return


def load_website_lookup_cost() -> dict:
    """Real spend from the last "Look up website" batch run (Zahir's
    explicit ask 2026-07-31 - the button should show the actual $ from the
    last run, not a generic cost warning). {"cost": float, "count": int,
    "at": iso timestamp} - default to zeroed-out values before any run."""
    return read_json(WEBSITE_LOOKUP_COST_PATH, default={"cost": 0.0, "count": 0, "at": None})


def save_website_lookup_cost(cost: float, count: int) -> None:
    with locked("website_lookup_cost"):
        write_json(WEBSITE_LOOKUP_COST_PATH, {
            "cost": cost,
            "count": count,
            "at": datetime.now(timezone.utc).isoformat(),
        })


def set_website(company_name: str, website: str) -> None:
    """Caches a looked-up company website URL on the account record - ""
    is a valid cached value meaning "searched, genuinely not found",
    distinct from the "website" key being absent entirely (never searched
    yet). See prospector.company_lookup.lookup_company_website() for the
    actual search."""
    with locked("target_accounts"):
        accounts = load_target_accounts()
        for acc in accounts:
            if acc["company_name"].lower() == company_name.lower():
                acc["website"] = website
                _save_all(accounts)
                return


def _normalize_company(name: str) -> str:
    """Light punctuation/whitespace normalization for company-name
    matching - same approach as linkedin/connections.py's and
    learn_engine.py's near-identical helpers (kept separate rather than a
    cross-package import, same precedent as those two)."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[.,]", "", name.lower())).strip()


PAUSED_STATUSES = ("disqualified", "stale")  # accounts Zahir has decided to
# stop actively watching - "contacted" is deliberately excluded, that's
# active engagement (Zahir IS doing something about it), not a pause.


def _new_signals_since_status_change(account: dict) -> list[dict]:
    """PRD S16a #15 (2026-08-10): a signal added via add_signal() to an
    already-paused account was silently absorbed before this - status
    stays sticky (correct, see MANUAL_STATUSES), but nothing ever surfaced
    that genuinely new evidence had arrived, unlike the job-posting check
    below. Needs status_updated_at (#20, added same day) as the "did Zahir
    already know about this when he made the call" cutoff - an account
    paused before that field existed (or whose status hasn't changed
    since) has no reference point, so this returns [] for it rather than
    guessing, same "pre-timestamp records are excluded from timestamp-
    dependent checks" convention as kpis.py/applications.py. Malformed/
    unparseable dates are skipped individually, not fatal to the whole
    account's check - a live Claude Code session can pass any string as
    date_observed, not just this module's own now()-generated ones."""
    status_updated_at = account.get("status_updated_at")
    if not status_updated_at:
        return []
    try:
        cutoff = datetime.fromisoformat(status_updated_at)
    except ValueError:
        return []

    out = []
    for sig in account["signals"]:
        raw = sig.get("date_observed")
        if not raw:
            continue
        try:
            is_new = datetime.fromisoformat(raw) > cutoff
        except (ValueError, TypeError):  # TypeError: naive/aware mismatch
            continue
        if is_new:
            out.append(sig)
    return out


def find_paused_accounts_with_new_activity(
    target_accounts: list[dict], jobs: list[dict], applications: list[dict],
) -> list[dict]:
    """Real gap found live 2026-08-09: a manual "paused" status
    (disqualified/stale - see PAUSED_STATUSES) is sticky on purpose (see
    MANUAL_STATUSES above - a new signal never silently overwrites Zahir's
    own call) but sticky isn't the same as invisible. UCB Pharma, BAUSCH,
    and BeOne Medicines USA were all disqualified for good reasons at the
    time (a stale trial, an already-approved-drug signal that isn't
    forward-looking) - then each later picked up a REAL job posting,
    BeOne's now an application Zahir has under review, with nothing ever
    surfacing that mismatch back to him. This doesn't change any status -
    manual stays manual - it just flags "here's new evidence you didn't
    have when you made this call" for Zahir to re-review himself.

    Two kinds of "new evidence" are checked, both needing status_updated_at
    (#20) as the before/after cutoff:
    1. A real job posting or application matching the company name (same
       normalize-and-substring approach as commercial_hiring.py/
       connections.py). A match is only reported if its ref
       ("<source>:<job_id>") ISN'T already one of the account's own signal
       refs - otherwise a company paused BECAUSE of a specific posting
       (e.g. "Securitas", disqualified as an unrelated industry whose job
       title matched a keyword by coincidence) would re-flag itself
       forever on the exact same posting that already got reviewed and
       rejected. Found by testing against real data before this filter
       was added - Securitas was a false positive until this ref check
       went in.
    2. A signal added to the account's own signals list AFTER the status
       was last set (#15, 2026-08-10) - see _new_signals_since_status_change.

    Originally "disqualified"-only (2026-08-09); extended to also cover
    "stale" (#16, 2026-08-10) - a stale account has the identical "we
    stopped watching this, new evidence should resurface it" shape.
    "contacted" is deliberately NOT covered - that status means Zahir is
    actively engaged, not paused, a different situation this check isn't
    meant to nudge.

    Performance (#10, found live 2026-08-09 costing ~675ms unconditionally
    on every Prospector tab render, O(paused accounts x jobs), growing
    with real job volume): each job's normalized organization name is
    computed ONCE up front (job_index below) instead of being re-normalized
    for every paused account - this was previously the dominant cost, not
    the substring check itself. The caller (src/ui/app.py) additionally
    wraps this in st.cache_data so a Streamlit rerun that doesn't change
    target_accounts/jobs/applications doesn't recompute at all - this
    function itself no longer self-loads target_accounts (previously via
    load_target_accounts()) specifically so that caching can key off the
    real data instead of silently ignoring target_accounts changes."""
    paused = [a for a in target_accounts if a["status"] in PAUSED_STATUSES]
    if not paused:
        return []

    app_lookup = {(a.get("source"), a.get("job_id")): a for a in applications}

    job_index = []
    for job in jobs:
        org = job.get("organization")
        if not org:
            continue
        job_norm = _normalize_company(org)
        if job_norm:
            job_index.append((job, job_norm))

    out = []
    for acc in paused:
        norm = _normalize_company(acc["company_name"])
        if not norm:
            continue
        known_refs = {s.get("ref") for s in acc["signals"] if s.get("ref")}

        matching_jobs = []
        for job, job_norm in job_index:
            if not (norm in job_norm or job_norm in norm):
                continue
            job_ref = f"{job.get('source')}:{job.get('job_id')}"
            if job_ref in known_refs:
                continue
            app = app_lookup.get((job.get("source"), job.get("job_id")))
            matching_jobs.append({
                "source": job.get("source"),
                "job_id": job.get("job_id"),
                "organization": job.get("organization"),
                "title": job.get("title"),
                "application_status": app["status"] if app else None,
            })

        new_signals = _new_signals_since_status_change(acc)

        if matching_jobs or new_signals:
            out.append({
                "company_name": acc["company_name"],
                "status": acc["status"],
                "notes": acc.get("notes"),
                "matching_jobs": matching_jobs,
                "new_signals": new_signals,
            })

    return out
