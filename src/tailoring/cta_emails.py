"""Local JSON-backed store for Gmail call-to-action emails flagged by the
panga-gmail-cta-scan scheduled task (interview invites, assessment requests,
offers, rejections, recruiter questions) - PRD-adjacent extension so these
show up on the Results screen instead of only as a push notification +
Gmail label. Nothing here changes Gmail state; it's a local mirror for
display, dismissed by the user once they've dealt with it in their inbox.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CTA_EMAILS_PATH = PROJECT_ROOT / "data" / "cta_emails" / "cta_emails.json"


def load_cta_emails() -> list[dict]:
    if not CTA_EMAILS_PATH.exists():
        return []
    return json.loads(CTA_EMAILS_PATH.read_text(encoding="utf-8"))


def _save_all(emails: list[dict]) -> None:
    CTA_EMAILS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CTA_EMAILS_PATH.write_text(json.dumps(emails, indent=2), encoding="utf-8")


def add_cta_email(
    thread_id: str,
    subject: str,
    sender: str,
    snippet: str,
    date: str,
    category: str,
) -> None:
    """Upserts by thread_id (idempotent if the scan somehow revisits a
    thread). category is a short label like "rejection", "interview_request",
    "assessment_request", "offer", "recruiter_question"."""
    emails = load_cta_emails()
    gmail_link = f"https://mail.google.com/mail/u/0/#all/{thread_id}"
    for e in emails:
        if e["thread_id"] == thread_id:
            e.update(subject=subject, sender=sender, snippet=snippet, date=date, category=category, gmail_link=gmail_link)
            _save_all(emails)
            return

    emails.append({
        "thread_id": thread_id,
        "subject": subject,
        "sender": sender,
        "snippet": snippet,
        "date": date,
        "category": category,
        "gmail_link": gmail_link,
        "dismissed": False,
    })
    _save_all(emails)


def dismiss_cta_email(thread_id: str) -> None:
    emails = load_cta_emails()
    for e in emails:
        if e["thread_id"] == thread_id:
            e["dismissed"] = True
            _save_all(emails)
            return


def get_active_cta_emails() -> list[dict]:
    active = [e for e in load_cta_emails() if not e.get("dismissed")]
    return sorted(active, key=lambda e: e.get("date") or "", reverse=True)
