"""Local JSON-backed store for call-to-action emails flagged by the
scheduled inbox scan (interview invites, assessment requests, offers,
rejections, recruiter questions), across every configured account -
Gmail, Outlook/Hotmail, and any number of Yahoo/other IMAP accounts (see
docs/multi-provider-accounts-scope.md and src/inbox_accounts.py) - not
just Gmail, despite the module's original Gmail-only name. PRD-adjacent
extension so these show up on the Panga dashboard instead of only as a
push notification + inbox label/category.

Two actions the dashboard can request, both executed by the scheduled
fulfillment task (the dashboard itself has no live inbox access):
- archive_requested: "Dismiss" was clicked - archive the thread/message +
  mark it handled in the source inbox so it's out of the inbox but still
  browsable here.
- draft_requested: "Draft reply" was clicked - compose and create a real
  draft (never auto-sent) tailored to the email's category, in whichever
  provider's inbox it came from.

A third loop runs the other direction: the fulfillment task checks whether
drafts it created are still sitting in that provider's Drafts folder. Once
Zahir sends (or deletes) one himself, get_awaiting_draft_send()/
mark_draft_sent() let that resolve itself on the dashboard - Zahir never
has to remember to come back and click Dismiss for those.

`provider` ("gmail" | "microsoft" | "imap") and `account` (a fixed label
for Gmail/Microsoft's single-account modules, or the actual email address
for an IMAP account) tell the fulfillment task which client to route a
given record to - see inbox_accounts.py, which is the only thing that
should ever pass provider/account values other than the defaults.
`thread_id` stays the sole lookup key across all providers (not
namespaced by provider/account) - a deliberate simplification: Gmail
thread ids, Outlook conversation ids, and IMAP UIDs are shaped
differently enough (long hex vs. base64-ish vs. small integers) that a
real collision is not a realistic risk at this single-user scale, so this
keeps the existing simple key rather than rippling a composite-key change
through the whole store.

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
    provider: str = "gmail",
    account: str = "gmail",
    web_link: str | None = None,
) -> None:
    """Upserts by thread_id (idempotent if the scan somehow revisits a
    thread). category is one of "rejection", "interview_request",
    "assessment_request", "offer", "recruiter_question". message_id is the
    latest message's id in the thread, used as replyToMessageId when a draft
    is later created. `provider`/`account` default to Gmail for backward
    compatibility with every existing call site and stored record predating
    multi-provider support - inbox_accounts.py passes real values for
    Outlook/IMAP accounts. `web_link` is a best-effort "open this in your
    provider's webmail" URL - None where no reliable deep link exists (see
    inbox_accounts.py's per-provider adapters), in which case the UI falls
    back to a generic message instead of a broken link."""
    with locked("cta_emails"):
        emails = load_cta_emails()
        if web_link is None and provider == "gmail":
            web_link = f"https://mail.google.com/mail/u/0/#all/{thread_id}"
        existing = _find(emails, thread_id)
        if existing:
            existing.update(
                subject=subject, sender=sender, snippet=snippet, date=date, category=category,
                provider=provider, account=account, web_link=web_link,
            )
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
            "provider": provider,
            "account": account,
            "web_link": web_link,
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


def mark_draft_created(thread_id: str, draft_id: str, draft_link: str | None = None) -> None:
    """`draft_link` is a best-effort "open this draft" URL - the caller
    (inbox_accounts.py's adapters) computes it per-provider. Left as None
    for a record whose own `provider` is anything but Gmail (no reliable
    webmail deep link exists generically for Outlook/IMAP, unlike Gmail's
    stable compose-URL format) - the UI falls back to a generic message
    rather than a broken link in that case. Only synthesizes the legacy
    Gmail URL automatically when the caller doesn't pass one AND the
    record's own provider is Gmail, for backward compatibility with call
    sites predating multi-provider support."""
    with locked("cta_emails"):
        emails = load_cta_emails()
        e = _find(emails, thread_id)
        if e:
            e["draft_requested"] = False
            e["draft_created"] = True
            e["draft_id"] = draft_id
            if draft_link is None and e.get("provider", "gmail") == "gmail":
                draft_link = f"https://mail.google.com/mail/u/0/#drafts?compose={draft_id}"
            e["draft_link"] = draft_link
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
