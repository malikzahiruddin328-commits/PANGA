"""Gmail's official API (native-packaging branch, 2026-07-31) - replaces the
Claude Code Gmail MCP connector, which a standalone build can't carry (no
live Claude Code session backs it). Mirrors the subset of connector tool
behavior the panga-gmail-cta-scan / panga-cta-fulfillment SKILL.md prompts
actually used (see docs/email-monitoring-task.md): search_threads,
get_thread, list_labels, create_label, label_thread, unlabel_thread,
create_draft, list_drafts.

OAuth model: Zahir creates his own Google Cloud project + OAuth client
(Desktop app type) once, downloads the client-secret JSON to
data/gmail/credentials.json - same one-time per-user setup as
ANTHROPIC_API_KEY/USAJOBS_API_KEY elsewhere in this app, this module never
creates or manages that registration itself. The first real call opens a
browser for Zahir to grant consent (google_auth_oauthlib's local-server
flow); the resulting token is cached at data/gmail/token.json (encrypted via
security.crypto_store, since it's a live credential) and silently refreshed
after that - no repeat consent needed until the refresh token itself is
revoked.
"""

import base64
import json
import re
import shutil
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from security.crypto_store import read_text, write_text
from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS_PATH = PROJECT_ROOT / "data" / "gmail" / "credentials.json"
TOKEN_PATH = PROJECT_ROOT / "data" / "gmail" / "token.json"

# gmail.modify covers reading, labeling, and archiving (removing INBOX);
# gmail.compose covers creating/listing drafts. Neither includes sending a
# message outright or changing account settings - matches the "read/label/
# draft, never send automatically" safety property the original SKILL.md
# prompts were explicit about.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]

_MAX_PAGES = 20  # hard bound on any paginated list call - real inboxes are
                  # finite, but a bug in Gmail's nextPageToken (or in this
                  # code) must not turn a page loop into an unbounded one.


class GmailNotConfigured(Exception):
    pass


def is_configured() -> bool:
    return CREDENTIALS_PATH.exists()


# Google's own download names every OAuth-client client-secret file this
# way ("client_secret_<client-id>.apps.googleusercontent.com.json") -
# distinctive enough that matching on it in ~/Downloads is a safe,
# low-false-positive way to spot the file Zahir just downloaded, for the
# setup wizard's auto-detect step (Settings tab).
_DOWNLOADED_CREDENTIALS_GLOB = "client_secret_*.json"


def find_downloaded_credentials() -> list[Path]:
    """Scans the current Windows account's Downloads folder for a file
    matching Google's OAuth client-secret naming pattern, newest first.
    Returns an empty list if the folder doesn't exist or nothing matches -
    never raises, since this is just a convenience scan for the wizard."""
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return []
    hits = list(downloads.glob(_DOWNLOADED_CREDENTIALS_GLOB))
    return sorted(hits, key=lambda p: p.stat().st_mtime, reverse=True)


def install_credentials_from_path(path: Path) -> None:
    """Moves (not copies) a downloaded client-secret JSON into
    data/gmail/credentials.json - moving rather than copying so a live
    OAuth credential doesn't end up sitting in two places (the general,
    less-protected Downloads folder, and the app's own data dir)."""
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(CREDENTIALS_PATH))


def install_credentials_from_bytes(data: bytes) -> None:
    """Saves an uploaded client-secret JSON (e.g. via a Streamlit file
    uploader) into data/gmail/credentials.json. Raises ValueError if it
    isn't valid JSON - fail loudly on a bad upload rather than silently
    saving something get_credentials() will just fail to parse later."""
    try:
        json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError("That file isn't valid JSON - is it the right download?") from exc
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_bytes(data)


def _load_cached_credentials() -> Credentials | None:
    token_json = read_text(TOKEN_PATH, default=None)
    if token_json is None:
        return None
    return Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)


def _save_credentials(creds: Credentials) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_text(TOKEN_PATH, creds.to_json())


def get_credentials() -> Credentials:
    """Returns valid credentials, refreshing or running the one-time consent
    flow as needed. Raises GmailNotConfigured if Zahir hasn't placed his
    downloaded OAuth client JSON at data/gmail/credentials.json yet.

    The load-check-refresh-save sequence runs under a lock (2026-08-06,
    found proactively while reviewing what the new manual sync button
    changes about concurrency exposure - see docs/manual-sync-button-scope.md's
    sibling issue): the scheduled fulfillment task and a manual "Send and
    receive" click are now two separate processes that can both need to
    refresh this same token at close to the same moment, and the previous
    unlocked read-modify-write here was exactly the class of race
    CLAUDE.md's standing rule already calls out for every other shared
    store - this one had just been missed. Locking around the network
    refresh call itself (not just the file write) is deliberate: it
    serializes concurrent refresh attempts, not just concurrent writes."""
    if not is_configured():
        raise GmailNotConfigured(
            "No Gmail OAuth client found. In Google Cloud Console, create a "
            "project, enable the Gmail API, create an OAuth client (type "
            "'Desktop app'), download its JSON, and save it as "
            f"{CREDENTIALS_PATH}."
        )

    with locked("gmail_token"):
        creds = _load_cached_credentials()
        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_credentials(creds)
                return creds
            except RefreshError:
                creds = None  # refresh token itself revoked/expired - fall through to re-consent

    # No valid or refreshable cached token - the one-time interactive
    # consent flow, deliberately OUTSIDE the lock above: this needs a real
    # person clicking through a browser window, which can take far longer
    # than the lock's own wait bound (security/file_lock.py's
    # _MAX_WAIT_SECONDS), and two processes both hitting first-time
    # consent at once doesn't corrupt anything even unlocked - each just
    # uses its own in-memory result regardless of which write to
    # token.json ends up "winning".
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    # Generous timeout - this is a real person clicking through Google's
    # consent screen (reading scopes, maybe switching accounts), not an
    # automated redirect; the library's default is too short for that.
    creds = flow.run_local_server(port=0, timeout_seconds=300)
    _save_credentials(creds)
    return creds


def get_service():
    return build("gmail", "v1", credentials=get_credentials())


def _header(headers: list[dict], name: str) -> str | None:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def _decode_body(payload: dict) -> str:
    """Prefers the first text/plain part; falls back to text/html with tags
    stripped (crude but adequate - these are read for classification, not
    rendered) if no plain-text part exists. Returns "" if no decodable body
    part is found at all (e.g. an attachment-only message)."""
    def _walk(part) -> tuple[str | None, str | None]:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime == "text/plain" and data:
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace"), None
        if mime == "text/html" and data:
            html = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
            return None, html
        plain_found, html_found = None, None
        for sub in part.get("parts", []) or []:
            p, h = _walk(sub)
            plain_found = plain_found or p
            html_found = html_found or h
        return plain_found, html_found

    plain, html = _walk(payload)
    if plain:
        return plain
    if html:
        # Strip <style>/<script> blocks WHOLE (tag stripping alone leaves
        # their raw CSS/JS content behind as visible "text" - found live on
        # a real ATS-template email whose ~40 lines of inline CSS were
        # otherwise dumped straight into the classifier's input ahead of
        # the actual message).
        html = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _thread_summary(service, thread_id: str) -> dict:
    """One row's worth of what the old MCP search_threads tool returned:
    subject/sender/date/snippet of the thread's LATEST message, plus its
    message_id (used as replyToMessageId when a draft is later created)
    and its label_ids (used by inbox_accounts.py to filter out already-
    reviewed threads client-side - see search_threads' docstring below for
    why the query's own -label: term can't be trusted to do this)."""
    thread = service.users().threads().get(
        userId="me", id=thread_id, format="metadata", metadataHeaders=["Subject", "From", "Date"],
    ).execute()
    messages = thread.get("messages", [])
    latest = messages[-1] if messages else {}
    headers = latest.get("payload", {}).get("headers", [])
    return {
        "thread_id": thread_id,
        "message_id": latest.get("id"),
        "subject": _header(headers, "Subject") or "(no subject)",
        "sender": _header(headers, "From") or "",
        "date": _header(headers, "Date") or "",
        # Real bug found live 2026-08-01: Gmail's API only populates
        # "snippet" on individual messages, never on the thread resource
        # itself (thread.get("snippet") is always None/empty regardless of
        # format) - reading it off `thread` silently starved every
        # classification call of body-preview content, leaving it with
        # only subject+sender to go on. The message's own snippet is what
        # actually has real content.
        "snippet": latest.get("snippet", ""),
        # The LATEST message's own labelIds, not any earlier message's -
        # see search_threads' docstring for why this is the field that
        # actually needs checking.
        "label_ids": latest.get("labelIds", []),
    }


def search_threads(query: str, max_results: int = 50) -> list[dict]:
    """Mirrors the connector's search_threads tool: returns thread summaries
    (subject/sender/date/snippet/message_id/label_ids) for threads matching
    `query` (Gmail search syntax, e.g. "-in:spam -in:trash newer_than:2d
    in:inbox"), newest first, up to max_results.

    Deliberately excludes a "-label:X" term from the queries inbox_accounts.py
    builds (real bug found 2026-08-07 via the scheduled CTA scan's own run
    report): Gmail's threads.modify labels a thread's messages as they exist
    at that moment, but "-label:X" in a search query matches at THREAD
    granularity - a thread stays excluded only once EVERY message in it
    carries the label. A thread that was labeled Panga/Reviewed, then later
    got a genuinely new reply (which arrives unlabeled), still has an
    unlabeled message in it - so "-label:Panga/Reviewed" kept matching that
    whole thread again, on every run, even though the "reviewed" run had
    already processed everything that existed at the time. Concretely: every
    gmail_cta_scan.py run was re-classifying (real LLM calls) the same ~80
    already-processed threads for nothing, 3x/day, compounding daily.
    inbox_accounts.py now checks label_ids on the LATEST message client-side
    instead - which is also the semantically correct check (a thread with a
    genuinely new unreviewed message SHOULD resurface, since there's new
    content to review; this fix gets that right as a side effect, not just
    the wasted-token case)."""
    service = get_service()
    try:
        response = service.users().threads().list(userId="me", q=query, maxResults=max_results).execute()
    except HttpError as exc:
        raise GmailNotConfigured(f"Gmail search failed: {exc}") from exc
    return [_thread_summary(service, t["id"]) for t in response.get("threads", [])]


def get_thread(thread_id: str) -> dict:
    """Full thread content for ambiguous classification calls - every
    message's subject/sender/date/snippet/decoded plain-text body, oldest
    first (matches Gmail's own thread ordering)."""
    service = get_service()
    thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    messages = []
    for msg in thread.get("messages", []):
        headers = msg.get("payload", {}).get("headers", [])
        messages.append({
            "message_id": msg.get("id"),
            "subject": _header(headers, "Subject") or "(no subject)",
            "sender": _header(headers, "From") or "",
            "date": _header(headers, "Date") or "",
            "snippet": msg.get("snippet", ""),
            "body": _decode_body(msg.get("payload", {})),
        })
    return {"thread_id": thread_id, "messages": messages}


def list_labels() -> list[dict]:
    service = get_service()
    response = service.users().labels().list(userId="me").execute()
    return [{"id": l["id"], "name": l["name"]} for l in response.get("labels", [])]


def create_label(name: str) -> str:
    """Returns the new label's id. Gmail rejects creating a label that
    already exists by name - callers should check list_labels() first (same
    "check with list_labels, create if missing" pattern the original
    SKILL.md prompts used)."""
    service = get_service()
    label = service.users().labels().create(userId="me", body={
        "name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show",
    }).execute()
    return label["id"]


def ensure_label(name: str) -> str:
    """Returns the id of the label named `name`, creating it first if it
    doesn't exist yet - the "check with list_labels, create if missing"
    pattern both original SKILL.md prompts used."""
    for label in list_labels():
        if label["name"] == name:
            return label["id"]
    return create_label(name)


def label_thread(thread_id: str, label_ids: list[str]) -> None:
    service = get_service()
    service.users().threads().modify(userId="me", id=thread_id, body={"addLabelIds": label_ids}).execute()


def unlabel_thread(thread_id: str, label_ids: list[str]) -> None:
    service = get_service()
    service.users().threads().modify(userId="me", id=thread_id, body={"removeLabelIds": label_ids}).execute()


def create_draft(to: str, subject: str, body: str, reply_to_message_id: str | None = None) -> str:
    """Creates a real Gmail draft (never sent). `to` should be a bare email
    address - if given a "Name <email@domain.com>" form instead, only the
    address part is used (create_draft's underlying message headers reject
    the display-name form for the To: field in some clients' strict
    parsing, matching the original SKILL.md's own explicit extraction
    step). When reply_to_message_id is given, the draft is threaded onto
    that message (Gmail thread id + In-Reply-To/References headers) so it
    reads as a genuine reply rather than a new message. Returns the new
    draft's id."""
    _, address = parseaddr(to)
    address = address or to

    message = MIMEText(body)
    message["to"] = address
    message["subject"] = subject

    thread_id = None
    if reply_to_message_id:
        service = get_service()
        original = service.users().messages().get(
            userId="me", id=reply_to_message_id, format="metadata", metadataHeaders=["Message-ID", "References"],
        ).execute()
        thread_id = original.get("threadId")
        original_headers = original.get("payload", {}).get("headers", [])
        original_message_id_header = _header(original_headers, "Message-ID")
        if original_message_id_header:
            message["In-Reply-To"] = original_message_id_header
            references = _header(original_headers, "References")
            message["References"] = f"{references} {original_message_id_header}" if references else original_message_id_header
    else:
        service = get_service()

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    draft_body = {"message": {"raw": raw}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    draft = service.users().drafts().create(userId="me", body=draft_body).execute()
    return draft["id"]


def list_drafts() -> list[str]:
    """Returns every current draft's id, for the fulfillment loop's
    reconciliation step (comparing outstanding draft_ids against what's
    actually still in Gmail). Paginated, bounded to _MAX_PAGES pages so a
    pagination bug can't turn this into an unbounded loop."""
    service = get_service()
    ids: list[str] = []
    page_token = None
    for _ in range(_MAX_PAGES):
        response = service.users().drafts().list(userId="me", pageToken=page_token).execute()
        ids.extend(d["id"] for d in response.get("drafts", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return ids
