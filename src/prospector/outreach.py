"""Local JSON-backed store for the `outreach` table (PRD §16b): one record
per contact reached out to, linked to EITHER a job or a target account (at
least one required). Encrypted at rest (PRD §7) via security.crypto_store.

Status lifecycle (extended from PRD §16b's original drafted/sent/
responded/no-response): "planned" added as the state before any draft
exists - Zahir logging "I want to reach out to X" is itself worth
recording even before a message is drafted. Same kind of small,
documented lifecycle extension as applications.py's real status set grew
beyond the PRD's original sketch.

Email drafting REUSES the Gmail-draft mechanism from cta_emails.py/the
panga-cta-fulfillment scheduled task (§14) rather than a second pathway -
same request_draft() -> scheduled task creates the real Gmail draft ->
get_awaiting_draft_send() reconciliation loop, just a second table it
checks. LinkedIn/phone/in-person outreach is logged here but Panga can
never draft or send on those channels - Zahir handles those himself and
just records status manually.
"""
import secrets
from datetime import datetime, timezone
from pathlib import Path

from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTREACH_PATH = PROJECT_ROOT / "data" / "outreach" / "outreach.json"


def load_outreach() -> list[dict]:
    return read_json(OUTREACH_PATH, default=[])


def _save_all(records: list[dict]) -> None:
    write_json(OUTREACH_PATH, records)


def _find(records: list[dict], outreach_id: str) -> dict | None:
    for r in records:
        if r["outreach_id"] == outreach_id:
            return r
    return None


def add_outreach(
    contact_name: str,
    channel: str,
    job_source: str | None = None,
    job_id: str | None = None,
    target_account_name: str | None = None,
    contact_title: str | None = None,
    contact_email: str | None = None,
    notes: str | None = None,
) -> str:
    """Creates an outreach record with status "planned". Requires either
    (job_source AND job_id) or target_account_name - outreach must be
    anchored to one or the other. Returns the new outreach_id."""
    if not ((job_source and job_id) or target_account_name):
        raise ValueError("outreach needs either a (job_source, job_id) pair or a target_account_name")

    records = load_outreach()
    outreach_id = secrets.token_hex(8)
    now = datetime.now(timezone.utc).isoformat()
    records.append({
        "outreach_id": outreach_id,
        "job_source": job_source,
        "job_id": job_id,
        "target_account_name": target_account_name,
        "contact_name": contact_name,
        "contact_title": contact_title,
        "contact_email": contact_email,
        "channel": channel,
        "status": "planned",
        "content": None,
        "strategy_tag": None,
        "notes": notes,
        "created_at": now,
        "status_updated_at": now,
        "draft_requested": False,
        "gmail_draft_id": None,
        "gmail_draft_link": None,
    })
    _save_all(records)
    return outreach_id


def update_status(outreach_id: str, status: str, notes: str | None = None) -> None:
    records = load_outreach()
    r = _find(records, outreach_id)
    if r:
        if r["status"] != status:
            r["status_updated_at"] = datetime.now(timezone.utc).isoformat()
        r["status"] = status
        if notes is not None:
            r["notes"] = notes
        _save_all(records)


def request_draft(outreach_id: str) -> None:
    """Flags an email-channel outreach record for the panga-cta-fulfillment
    scheduled task to compose and create a real Gmail draft on its next
    run - same "prepare but don't submit" rule as CTA replies."""
    records = load_outreach()
    r = _find(records, outreach_id)
    if r:
        r["draft_requested"] = True
        _save_all(records)


def get_pending_draft_requests() -> list[dict]:
    return [r for r in load_outreach() if r.get("draft_requested") and not r.get("gmail_draft_id")]


def mark_draft_created(outreach_id: str, draft_id: str) -> None:
    records = load_outreach()
    r = _find(records, outreach_id)
    if r:
        r["draft_requested"] = False
        r["gmail_draft_id"] = draft_id
        r["gmail_draft_link"] = f"https://mail.google.com/mail/u/0/#drafts?compose={draft_id}"
        if r["status"] == "planned":
            r["status_updated_at"] = datetime.now(timezone.utc).isoformat()
            r["status"] = "drafted"
        _save_all(records)


def get_awaiting_draft_send() -> list[dict]:
    """Outreach records with a draft sitting in Gmail, still marked
    "drafted" (not yet confirmed sent) - the fulfillment task checks each
    gmail_draft_id against Gmail's live drafts list, same reconciliation
    pattern as cta_emails.get_awaiting_draft_send()."""
    return [r for r in load_outreach() if r.get("gmail_draft_id") and r["status"] == "drafted"]


def mark_draft_sent(outreach_id: str) -> None:
    update_status(outreach_id, "sent")


def get_outreach_for_target_account(company_name: str) -> list[dict]:
    return [r for r in load_outreach() if (r.get("target_account_name") or "").lower() == company_name.lower()]


def get_outreach_for_job(source: str, job_id: str) -> list[dict]:
    return [r for r in load_outreach() if r.get("job_source") == source and r.get("job_id") == job_id]
