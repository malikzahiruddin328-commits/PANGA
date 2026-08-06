"""Shared fulfillment logic for Call-to-Action + Prospector outreach
requests - archive/draft/reconcile, running against every configured
inbox account via inbox_accounts.py. Used by BOTH scripts/cta_fulfillment.py
(the 2x/day scheduled run, throttled down from every 10 minutes 2026-08-05
for cost reasons) and the dashboard's synchronous "Send and receive"
button (src/ui/app.py's Call to Action tab) - one shared implementation,
not two, so a manual sync and a scheduled run can never drift apart in
behavior. See docs/manual-sync-button-scope.md.

Reply/outreach composition (tailoring.cta_reasoning.draft_cta_reply,
prospector.outreach_reasoning.draft_outreach_email) was already a direct
Anthropic API call before this module existed - not something this module
had to convert from live Claude Code reasoning itself, despite that being
the original SKILL.md-era behavior. The one piece that genuinely was
still live-only until this module: composing a cold outreach intro
(SKILL.md's STEP 2C) - see outreach_reasoning.py, added alongside this.

Runs synchronously and in-process when called from the Streamlit button -
this is safe specifically because gmail_client.py/microsoft_client.py/
imap_client.py are already direct-API/OAuth/IMAP clients with no MCP or
live-session dependency (confirmed zero mailopoly/MCP references anywhere
in src/ before this was built). A run typically processes a handful of
items and completes in a few seconds to tens of seconds (dominated by the
LLM calls, not the mail API calls) - the caller is expected to show a
spinner, not assume this returns instantly.

Race safety: this module does not introduce any new concurrency. The
scheduled task and the button could in principle run at the same moment
(a manual click during the 8am/4pm window), but every store this touches
(cta_emails.json, outreach.json, applications.json) already goes through
security.file_lock.locked() read-modify-write sections regardless of
which caller is writing - unchanged by this module existing. SYNC_STATUS_PATH
below gets the same treatment for the same reason.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from inbox_accounts import configured_accounts
from prospector import outreach
from prospector.outreach_reasoning import draft_outreach_email
from search.job_store import load_jobs
from security.crypto_store import read_json, write_json
from security.file_lock import locked
from tailoring.cta_emails import (
    get_awaiting_draft_send,
    get_pending_archive_requests,
    get_pending_draft_requests,
    mark_archived,
    mark_draft_created,
    mark_draft_sent,
)
from tailoring.cta_reasoning import draft_cta_reply

PROJECT_ROOT = Path(__file__).resolve().parent
SYNC_STATUS_PATH = PROJECT_ROOT.parent / "data" / "fulfillment" / "sync_status.json"

_INVALID_EMAIL_MARKERS = ("noreply", "no-reply")


# ---- last-synced tracking ----

def get_last_synced_at() -> Optional[str]:
    """ISO 8601 timestamp of the last successful run (manual or
    scheduled), or None if fulfillment has never completed a run."""
    return read_json(SYNC_STATUS_PATH, default={}).get("last_synced_at")


def _record_sync_completed() -> None:
    with locked("fulfillment_sync_status"):
        write_json(SYNC_STATUS_PATH, {"last_synced_at": datetime.now(timezone.utc).isoformat()})


# ---- pending count (for the dashboard status card) ----

def get_pending_count() -> int:
    """Sum of everything a "Send and receive" click would act on -
    outreach has no archive-request concept (SKILL.md: "outreach isn't
    tied to inbox labels"), so this is CTA archive + CTA draft + outreach
    draft only."""
    return (
        len(get_pending_archive_requests())
        + len(get_pending_draft_requests())
        + len(outreach.get_pending_draft_requests())
    )


# ---- account routing ----

def _accounts_by_key(accounts: list) -> dict:
    return {(a.provider, a.account): a for a in accounts}


def _account_key(item: dict) -> tuple:
    return item.get("provider", "gmail"), item.get("account", "gmail")


# ---- CTA: archive ----

def fulfill_archive_requests(accounts_by_key: dict) -> int:
    failures = 0
    for item in get_pending_archive_requests():
        account = accounts_by_key.get(_account_key(item))
        if account is None:
            failures += 1
            continue
        try:
            account.archive(item["thread_id"])
            mark_archived(item["thread_id"])
        except Exception:  # noqa: BLE001 - one item's failure shouldn't stop the rest
            failures += 1
    return failures


# ---- CTA: draft ----

def _get_available_interview_slots() -> Optional[list]:
    """Best-effort Google Calendar lookup for interview_request replies -
    see docs/multi-provider-accounts-scope.md Part B. Returns None (not an
    empty list) on any failure or if Calendar isn't configured, so the
    caller falls back to draft_cta_reply()'s existing "ask them to propose
    times" behavior instead of a confusing empty offer. Never raises -
    a calendar problem should never block CTA draft creation."""
    try:
        import google_calendar_client as calendar_client

        if not calendar_client.is_configured():
            return None
        now = datetime.now()
        range_end = now + timedelta(days=7)
        busy = calendar_client.get_busy_intervals(now, range_end)
        free_windows = calendar_client.compute_free_windows(now, range_end, busy)
        slots = calendar_client.curate_believable_slots(free_windows, count=3)
        return [w.start.strftime("%A, %b %d, %I:%M %p") for w in slots]
    except Exception:  # noqa: BLE001 - see docstring
        return None


def fulfill_cta_draft_requests(accounts_by_key: dict) -> int:
    failures = 0
    pending = get_pending_draft_requests()
    interview_slots = _get_available_interview_slots() if any(item["category"] == "interview_request" for item in pending) else None
    for item in pending:
        account = accounts_by_key.get(_account_key(item))
        if account is None:
            failures += 1
            continue
        try:
            slots_for_item = interview_slots if item["category"] == "interview_request" else None
            reply_body = draft_cta_reply(item["category"], item["subject"], item["snippet"], available_slots=slots_for_item)
            draft_id, draft_link = account.create_reply_draft(
                item["sender"], f"Re: {item['subject']}", reply_body, item.get("message_id"),
            )
            mark_draft_created(item["thread_id"], draft_id, draft_link=draft_link)
        except Exception:  # noqa: BLE001
            failures += 1
    return failures


def reconcile_sent_drafts(accounts_by_key: dict) -> None:
    awaiting = get_awaiting_draft_send()
    if not awaiting:
        return
    current_ids_cache: dict = {}
    for item in awaiting:
        key = _account_key(item)
        if key not in current_ids_cache:
            account = accounts_by_key.get(key)
            if account is None:
                current_ids_cache[key] = None
                continue
            try:
                current_ids_cache[key] = set(account.list_current_draft_ids())
            except Exception:  # noqa: BLE001 - one account's failure shouldn't block reconciling the others
                current_ids_cache[key] = None
        current_ids = current_ids_cache[key]
        if current_ids is None:
            continue
        if item.get("draft_id") and item["draft_id"] not in current_ids:
            mark_draft_sent(item["thread_id"])


# ---- Prospector outreach: draft (SKILL.md STEP 2C) ----

def _looks_like_valid_email(address: Optional[str]) -> bool:
    if not address or "@" not in address:
        return False
    local, _, domain = address.partition("@")
    if not local or "." not in domain:
        return False
    return not any(marker in address.lower() for marker in _INVALID_EMAIL_MARKERS)


def _resolve_outreach_context(record: dict) -> str:
    """Plain-language reason Zahir is reaching out, for
    outreach_reasoning.draft_outreach_email - a target account's own
    notes if this outreach is account-linked, else the linked job's
    title/organization, else a generic fallback (should be rare - every
    outreach record requires one anchor or the other, see
    outreach.add_outreach)."""
    if record.get("target_account_name"):
        context = f"Target account: {record['target_account_name']}"
        if record.get("notes"):
            context += f". Notes: {record['notes']}"
        return context
    if record.get("job_source") and record.get("job_id"):
        job = next(
            (j for j in load_jobs() if j.get("source") == record["job_source"] and j.get("job_id") == record["job_id"]),
            None,
        )
        if job:
            return f"Reaching out about the {job.get('title', 'open role')} role at {job.get('organization', 'this company')}."
    return record.get("notes") or "General interest in opportunities at this company."


def fulfill_outreach_draft_requests(accounts_by_key: dict) -> int:
    """SKILL.md STEP 2C - cold outreach intro drafting. Skips (doesn't
    guess a fake address) when contact_email is missing or looks clearly
    invalid/automated, per that step's explicit instruction - a
    deliberately different rule from CTA replies, which draft regardless
    of how the sender address looks (replying to a real inbound thread is
    never guessing an address)."""
    failures = 0
    gmail_account = accounts_by_key.get(("gmail", "gmail"))
    fallback_account = gmail_account or next(iter(accounts_by_key.values()), None)
    if fallback_account is None:
        return 0  # nothing configured to send from - nothing to fulfill, not a failure

    for record in outreach.get_pending_draft_requests():
        if not _looks_like_valid_email(record.get("contact_email")):
            continue
        try:
            context = _resolve_outreach_context(record)
            body = draft_outreach_email(record["contact_name"], record.get("contact_title"), context)
            draft_id, draft_link = fallback_account.create_reply_draft(
                record["contact_email"], "Introduction - Zahir Uddin", body, None,
            )
            outreach.mark_draft_created(
                record["outreach_id"], draft_id,
                provider=fallback_account.provider, account=fallback_account.account, draft_link=draft_link,
            )
        except Exception:  # noqa: BLE001
            failures += 1
    return failures


def reconcile_sent_outreach_drafts(accounts_by_key: dict) -> None:
    awaiting = outreach.get_awaiting_draft_send()
    if not awaiting:
        return
    current_ids_cache: dict = {}
    for record in awaiting:
        key = _account_key(record)
        if key not in current_ids_cache:
            account = accounts_by_key.get(key)
            if account is None:
                current_ids_cache[key] = None
                continue
            try:
                current_ids_cache[key] = set(account.list_current_draft_ids())
            except Exception:  # noqa: BLE001
                current_ids_cache[key] = None
        current_ids = current_ids_cache[key]
        if current_ids is None:
            continue
        draft_id = record.get("draft_id") or record.get("gmail_draft_id")
        if draft_id and draft_id not in current_ids:
            outreach.mark_draft_sent(record["outreach_id"])


# ---- entry point ----

def run_full_fulfillment() -> dict:
    """The whole STEP 0-5 sequence (SKILL.md) in one call - used by both
    the scheduled script and the dashboard button. Returns a summary the
    caller can report to the user: {"archived": int, "cta_drafts": int,
    "outreach_drafts": int, "failures": int}. Always records a completed
    sync timestamp, even on a run with failures - "last synced" means
    "fulfillment last ran," not "last ran with zero errors," matching
    what a real Outlook-style Send/Receive status line means."""
    accounts_by_key = _accounts_by_key(configured_accounts())

    # Counted before acting, not after - most successfully-fulfilled items
    # will read back as 0 pending afterward, so "before" is what actually
    # tells the caller how much this run attempted (failures are reported
    # separately and are typically rare, so this reads as "roughly how
    # many succeeded" without needing to track per-item outcomes).
    pending_archives_before = len(get_pending_archive_requests())
    pending_cta_drafts_before = len(get_pending_draft_requests())
    pending_outreach_drafts_before = len(outreach.get_pending_draft_requests())

    failures = fulfill_archive_requests(accounts_by_key)
    failures += fulfill_cta_draft_requests(accounts_by_key)
    failures += fulfill_outreach_draft_requests(accounts_by_key)

    reconcile_sent_drafts(accounts_by_key)
    reconcile_sent_outreach_drafts(accounts_by_key)

    failures += fulfill_archive_requests(accounts_by_key)  # second pass - picks up anything reconciliation just queued

    _record_sync_completed()

    return {
        "archived": pending_archives_before,
        "cta_drafts": pending_cta_drafts_before,
        "outreach_drafts": pending_outreach_drafts_before,
        "failures": failures,
    }
