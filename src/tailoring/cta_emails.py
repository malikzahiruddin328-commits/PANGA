"""Local JSON-backed store for Gmail call-to-action emails flagged by the
panga-gmail-cta-scan scheduled task (interview invites, assessment requests,
offers, rejections, recruiter questions) - PRD-adjacent extension so these
show up on the Panga dashboard instead of only as a push notification +
Gmail label.

Two actions the dashboard can request, both executed by the panga-cta-
fulfillment scheduled task (the dashboard itself has no live Gmail access):
- archive_requested: "Dismiss" was clicked - archive the thread + apply the
  "Panga/Handled" label so it's out of the inbox but still browsable.
- draft_requested: "Draft reply" was clicked - compose and create a real
  Gmail draft (never auto-sent) tailored to the email's category.

A third loop runs the other direction: the fulfillment task checks whether
drafts it created are still sitting in Gmail's Drafts folder. Once Zahir
sends (or deletes) one himself, get_awaiting_draft_send()/mark_draft_sent()
let that resolve itself on the dashboard - Zahir never has to remember to
come back and click Dismiss for those.

Encrypted at rest (PRD §7) via security.crypto_store.
"""

from pathlib import Path

from security.crypto_store import read_json, write_json
from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CTA_EMAILS_PATH = PROJECT_ROOT / "data" / "cta_emails" / "cta_emails.json"


def load_cta_emails() -> list[dict]:
    return read_json(CTA_EMAILS_PATH, default=[])


def _save_all(emails: list[dict]) -> None:
    write_json(CTA_EMAILS_PATH, emails)


def _find(emails: list[dict], thread_id: str) -> dict | None:
    for e in emails:
        if e["thread_id"] == thread_id:
            return e
    return None


def add_cta_email(
    thread_id: str,
    subject: str,
    sender: str,
    snippet: str,
    date: str,
    category: str,
    message_id: str | None = None,
) -> None:
    """Upserts by thread_id (idempotent if the scan somehow revisits a
    thread). category is one of "rejection", "interview_request",
    "assessment_request", "offer", "recruiter_question". message_id is the
    latest message's id in the thread, used as replyToMessageId when a draft
    is later created."""
    with locked("cta_emails"):
        emails = load_cta_emails()
        gmail_link = f"https://mail.google.com/mail/u/0/#all/{thread_id}"
        existing = _find(emails, thread_id)
        if existing:
            existing.update(subject=subject, sender=sender, snippet=snippet, date=date, category=category, gmail_link=gmail_link)
            if message_id is not None:
                existing["message_id"] = message_id
            _save_all(emails)
            return

        emails.append({
            "thread_id": thread_id,
            "message_id": message_id,
            "subject": subject,
            "sender": sender,
            "snippet": snippet,
            "date": date,
            "category": category,
            "gmail_link": gmail_link,
            "dismissed": False,
            "archive_requested": False,
            "archived": False,
            "draft_requested": False,
            "draft_created": False,
            "draft_id": None,
            "draft_link": None,
            "draft_sent": False,
        })
        _save_all(emails)


def request_archive(thread_id: str) -> None:
    """Called when the user clicks "Dismiss": hides it from the dashboard
    immediately, and flags it for the scheduled task to actually archive +
    label "Panga/Handled" in Gmail on its next run."""
    with locked("cta_emails"):
        emails = load_cta_emails()
        e = _find(emails, thread_id)
        if e:
            e["dismissed"] = True
            e["archive_requested"] = True
            _save_all(emails)


def request_draft(thread_id: str) -> None:
    """Called when the user clicks "Draft reply": flags it for the scheduled
    task to compose and create a real Gmail draft on its next run."""
    with locked("cta_emails"):
        emails = load_cta_emails()
        e = _find(emails, thread_id)
        if e:
            e["draft_requested"] = True
            _save_all(emails)


def mark_archived(thread_id: str) -> None:
    with locked("cta_emails"):
        emails = load_cta_emails()
        e = _find(emails, thread_id)
        if e:
            e["archive_requested"] = False
            e["archived"] = True
            _save_all(emails)


def mark_draft_created(thread_id: str, draft_id: str) -> None:
    with locked("cta_emails"):
        emails = load_cta_emails()
        e = _find(emails, thread_id)
        if e:
            e["draft_requested"] = False
            e["draft_created"] = True
            e["draft_id"] = draft_id
            e["draft_link"] = f"https://mail.google.com/mail/u/0/#drafts?compose={draft_id}"
            _save_all(emails)


def get_pending_archive_requests() -> list[dict]:
    return [e for e in load_cta_emails() if e.get("archive_requested") and not e.get("archived")]


def get_pending_draft_requests() -> list[dict]:
    return [e for e in load_cta_emails() if e.get("draft_requested") and not e.get("draft_created")]


def get_awaiting_draft_send() -> list[dict]:
    """CTA emails with a draft sitting in Gmail that Zahir hasn't sent (or
    deleted) yet - still active/not dismissed. The fulfillment task checks
    each draft_id against Gmail's live drafts list; whichever ones are gone
    get resolved via mark_draft_sent so the dashboard's count reaches zero
    without Zahir having to click Dismiss himself."""
    return [e for e in load_cta_emails() if e.get("draft_created") and not e.get("dismissed")]


def mark_draft_sent(thread_id: str) -> None:
    """Called once the fulfillment task confirms a previously created draft
    is no longer in Gmail's Drafts folder - Zahir sent it (or deleted it).
    Resolves the CTA email the same way a manual Dismiss would (hides it from
    the dashboard) and queues the underlying thread for archive + "Panga/
    Handled" labeling, so Gmail and the dashboard end up telling the same
    story instead of the thread lingering in the inbox."""
    with locked("cta_emails"):
        emails = load_cta_emails()
        e = _find(emails, thread_id)
        if e:
            e["draft_sent"] = True
            e["dismissed"] = True
            if not e.get("archived"):
                e["archive_requested"] = True
            _save_all(emails)


def get_active_cta_emails() -> list[dict]:
    active = [e for e in load_cta_emails() if not e.get("dismissed")]
    return sorted(active, key=lambda e: e.get("date") or "", reverse=True)
