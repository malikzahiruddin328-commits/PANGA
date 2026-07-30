"""Local JSON-backed store for the `target_accounts` table (PRD §16a): one
record per company worth watching before it's posted a role, parallel to
`jobs`. Encrypted at rest (PRD §7) via security.crypto_store.

Qualification rule (v0, deliberately crude - see §16a): a company reaches
"qualified" once it has 2+ DISTINCT signal types (e.g. a late-stage trial
AND a regulatory filing), not just 2 instances of the same type (two Phase
3 trials alone is still just one kind of evidence). Below that, it's
"watching". Manual states ("contacted", "stale", "disqualified") are
sticky - a new signal arriving later never silently overwrites a status
Zahir set himself; that's his call, not automation's.
"""
from datetime import datetime, timezone
from pathlib import Path

from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TARGET_ACCOUNTS_PATH = PROJECT_ROOT / "data" / "target_accounts" / "target_accounts.json"

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
    account["status"] = "qualified" if len(distinct_types) >= 2 else "watching"


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
    accounts = load_target_accounts()

    account = next((a for a in accounts if a["company_name"].lower() == company_name.lower()), None)
    if account is None:
        account = {
            "company_name": company_name,
            "industry": industry,
            "status": "watching",
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
    himself (or reverting one back to watching/qualified)."""
    accounts = load_target_accounts()
    for acc in accounts:
        if acc["company_name"].lower() == company_name.lower():
            acc["status"] = status
            if notes is not None:
                acc["notes"] = notes
            _save_all(accounts)
            return
