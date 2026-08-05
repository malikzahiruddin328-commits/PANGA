"""Standalone replacement for the panga-cta-fulfillment Claude scheduled
task (native-packaging branch, 2026-07-31; extended 2026-08-04 to fulfill
requests across every configured inbox account, not just Gmail) - ported
step-for-step from
C:\\Users\\User\\.claude\\scheduled-tasks\\panga-cta-fulfillment\\SKILL.md
(see docs/email-monitoring-task.md), using inbox_accounts.py's
per-provider adapters (gmail_client.py/microsoft_client.py/imap_client.py -
no MCP anywhere, see that module's docstring) in place of the MCP
connector, and tailoring.cta_reasoning.draft_cta_reply() in place of live
Claude Code reasoning for reply composition.

Runs every 10 minutes (see docs/native-packaging-scope.md's Task Scheduler
wiring). Only ever creates DRAFTS, never sends - Zahir reviews and sends
every reply himself, matching the original SKILL.md's explicit safety
property. A cta_emails.json record written before multi-provider support
has no `provider`/`account` fields - both are read with a "gmail" default
throughout this file for that reason (see tailoring/cta_emails.py's own
backward-compatibility note).
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# See gmail_cta_scan.py's identical comment - real email subjects/senders
# contain arbitrary Unicode that a Windows console's default codepage can't
# encode; an unguarded print() would crash the whole run on one.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inbox_accounts import configured_accounts  # noqa: E402
from notifications import send_notification  # noqa: E402
from tailoring.cta_emails import (  # noqa: E402
    get_awaiting_draft_send,
    get_pending_archive_requests,
    get_pending_draft_requests,
    mark_archived,
    mark_draft_created,
    mark_draft_sent,
)
from tailoring.cta_reasoning import draft_cta_reply  # noqa: E402


def _log(message: str) -> None:
    print(message, flush=True)


def _account_key(item: dict) -> tuple[str, str]:
    return item.get("provider", "gmail"), item.get("account", "gmail")


def _accounts_by_key(accounts: list) -> dict:
    return {(a.provider, a.account): a for a in accounts}


def fulfill_archive_requests(accounts_by_key: dict) -> int:
    failures = 0
    for item in get_pending_archive_requests():
        account = accounts_by_key.get(_account_key(item))
        if account is None:
            _log(f"  archive skipped for thread {item['thread_id']}: {_account_key(item)} is no longer configured")
            failures += 1
            continue
        try:
            account.archive(item["thread_id"])
            mark_archived(item["thread_id"])
        except Exception as exc:  # noqa: BLE001 - one item's failure shouldn't stop the rest
            _log(f"  archive failed for thread {item['thread_id']}: {exc}")
            failures += 1
    return failures


def _get_available_interview_slots() -> list[str] | None:
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
    except Exception as exc:  # noqa: BLE001 - see docstring
        _log(f"  calendar lookup skipped: {exc}")
        return None


def fulfill_draft_requests(accounts_by_key: dict) -> int:
    failures = 0
    pending = get_pending_draft_requests()
    # Fetched once per run, not once per item - availability doesn't
    # meaningfully change within one 10-minute run, and every
    # interview_request item this run (across every account) can reuse
    # the same lookup (avoids calling the Calendar API once per pending
    # item for no benefit).
    interview_slots = _get_available_interview_slots() if any(item["category"] == "interview_request" for item in pending) else None
    for item in pending:
        account = accounts_by_key.get(_account_key(item))
        if account is None:
            _log(f"  draft skipped for thread {item['thread_id']}: {_account_key(item)} is no longer configured")
            failures += 1
            continue
        try:
            slots_for_item = interview_slots if item["category"] == "interview_request" else None
            reply_body = draft_cta_reply(item["category"], item["subject"], item["snippet"], available_slots=slots_for_item)
            draft_id, draft_link = account.create_reply_draft(
                item["sender"], f"Re: {item['subject']}", reply_body, item.get("message_id"),
            )
            mark_draft_created(item["thread_id"], draft_id, draft_link=draft_link)
        except Exception as exc:  # noqa: BLE001
            _log(f"  draft creation failed for thread {item['thread_id']}: {exc}")
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
            except Exception as exc:  # noqa: BLE001 - one account's failure shouldn't
                # block reconciling the others
                _log(f"  couldn't list current drafts for {key}: {exc}")
                current_ids_cache[key] = None
        current_ids = current_ids_cache[key]
        if current_ids is None:
            continue
        if item.get("draft_id") and item["draft_id"] not in current_ids:
            mark_draft_sent(item["thread_id"])


def run() -> None:
    accounts_by_key = _accounts_by_key(configured_accounts())

    _log("STEP 1 - Archive fulfillment")
    failures = fulfill_archive_requests(accounts_by_key)

    _log("STEP 2 - Draft fulfillment")
    failures += fulfill_draft_requests(accounts_by_key)

    _log("STEP 3 - Reconcile sent drafts")
    reconcile_sent_drafts(accounts_by_key)

    _log("STEP 4 - Second archive pass (picks up anything step 3 just queued)")
    failures += fulfill_archive_requests(accounts_by_key)

    _log("STEP 5 - Notify")
    if failures:
        send_notification(
            "Panga - CTA fulfillment issue",
            f"{failures} archive/draft action(s) failed this run - check the app.",
        )
    _log("Done.")


if __name__ == "__main__":
    run()
