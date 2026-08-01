"""Standalone replacement for the panga-cta-fulfillment Claude scheduled
task (native-packaging branch, 2026-07-31) - ported step-for-step from
C:\\Users\\User\\.claude\\scheduled-tasks\\panga-cta-fulfillment\\SKILL.md
(see docs/email-monitoring-task.md), using gmail_client.py in place of the
MCP connector and tailoring.cta_reasoning.draft_cta_reply() in place of
live Claude Code reasoning for reply composition.

Runs every 10 minutes (see docs/native-packaging-scope.md's Task Scheduler
wiring). Only ever creates Gmail DRAFTS, never sends - Zahir reviews and
sends every reply himself, matching the original SKILL.md's explicit
safety property.
"""

import sys
from pathlib import Path

# See gmail_cta_scan.py's identical comment - real email subjects/senders
# contain arbitrary Unicode that a Windows console's default codepage can't
# encode; an unguarded print() would crash the whole run on one.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import gmail_client  # noqa: E402
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


def fulfill_archive_requests(handled_label_id: str) -> int:
    failures = 0
    for item in get_pending_archive_requests():
        try:
            gmail_client.unlabel_thread(item["thread_id"], ["INBOX"])
            gmail_client.label_thread(item["thread_id"], [handled_label_id])
            mark_archived(item["thread_id"])
        except Exception as exc:  # noqa: BLE001 - one item's failure shouldn't stop the rest
            _log(f"  archive failed for thread {item['thread_id']}: {exc}")
            failures += 1
    return failures


def fulfill_draft_requests() -> int:
    failures = 0
    for item in get_pending_draft_requests():
        try:
            reply_body = draft_cta_reply(item["category"], item["subject"], item["snippet"])
            draft_id = gmail_client.create_draft(
                to=item["sender"], subject=f"Re: {item['subject']}", body=reply_body,
                reply_to_message_id=item.get("message_id"),
            )
            mark_draft_created(item["thread_id"], draft_id)
        except Exception as exc:  # noqa: BLE001
            _log(f"  draft creation failed for thread {item['thread_id']}: {exc}")
            failures += 1
    return failures


def reconcile_sent_drafts() -> None:
    awaiting = get_awaiting_draft_send()
    if not awaiting:
        return
    current_draft_ids = set(gmail_client.list_drafts())
    for item in awaiting:
        if item.get("draft_id") and item["draft_id"] not in current_draft_ids:
            mark_draft_sent(item["thread_id"])


def run() -> None:
    handled_label_id = gmail_client.ensure_label("Panga/Handled")

    _log("STEP 1 - Archive fulfillment")
    failures = fulfill_archive_requests(handled_label_id)

    _log("STEP 2 - Draft fulfillment")
    failures += fulfill_draft_requests()

    _log("STEP 3 - Reconcile sent drafts")
    reconcile_sent_drafts()

    _log("STEP 4 - Second archive pass (picks up anything step 3 just queued)")
    failures += fulfill_archive_requests(handled_label_id)

    _log("STEP 5 - Notify")
    if failures:
        send_notification(
            "Panga - CTA fulfillment issue",
            f"{failures} archive/draft action(s) failed this run - check the app.",
        )
    _log("Done.")


if __name__ == "__main__":
    run()
