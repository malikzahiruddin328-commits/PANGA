"""Generic IMAP mail client for Yahoo and "any other personal/ISP email"
(native-packaging's account-setup wizard, "Other" provider path - see
docs/multi-provider-accounts-scope.md Part A). Mirrors the subset of
gmail_client.py's behavior that has a real IMAP equivalent: search, read,
draft (never send), and a folder-move analog of Gmail's label/archive
behavior.

Auth model: IMAP + app-password (or the account's real password, for
providers that don't require an app password), not OAuth - this is the
whole reason Yahoo/"other" need a different module than Gmail/Outlook.
Multi-account by construction (unlike gmail_client.py, which only ever
has one Gmail account) - every function takes `email` as the account key.
Credentials are stored per-account at data/imap/<email>.json, encrypted at
rest via security.crypto_store (same treatment gmail_client.py gives its
own live token.json).

Real IMAP limits vs. Gmail's API, worth knowing before extending this:
- No native "thread" concept - generic IMAP is message-centric, not
  thread-centric. search_messages()/get_message() operate per-message,
  not per-thread; a caller wanting thread-like grouping would need to do
  it itself from References/In-Reply-To headers, not built in here.
- No native "label" concept - a message lives in exactly one folder at a
  time. move_to_folder() is the closest analog to Gmail's label_thread
  (move into a "Panga/Reviewed"-style folder instead of adding a label
  alongside INBOX).
- Drafts are created by IMAP APPEND straight into the Drafts folder with
  the \\Draft flag set - there is no SMTP send capability anywhere in this
  module, by construction, not just by convention.
"""

import email as email_lib
import imaplib
import re
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAP_DIR = PROJECT_ROOT / "data" / "imap"

_MAX_RESULTS = 200  # hard bound on any SEARCH result set this module will
                     # walk - matches gmail_client.py's _MAX_PAGES bound.
_CONNECT_TIMEOUT = 15


class ImapNotConfigured(Exception):
    pass


class ImapConnectionError(Exception):
    pass


def _account_path(email: str) -> Path:
    # Email addresses aren't safe filenames as-is (the "@" is fine on
    # Windows, but keep this defensive rather than trust every provider's
    # local-part never contains something stranger).
    safe = re.sub(r"[^A-Za-z0-9@._-]", "_", email.strip().lower())
    return IMAP_DIR / f"{safe}.json"


def is_configured(email: str) -> bool:
    return _account_path(email).exists()


def list_configured_accounts() -> list[str]:
    """Every email address with saved IMAP credentials, for the
    account-setup wizard's "already connected" display - IMAP is
    multi-account by construction (see module docstring), unlike Gmail/
    Microsoft's single-account modules, so the wizard needs this to show
    more than one at a time."""
    if not IMAP_DIR.is_dir():
        return []
    accounts = []
    for path in sorted(IMAP_DIR.glob("*.json")):
        creds = read_json(path, default=None)
        if creds and "email" in creds:
            accounts.append(creds["email"])
    return accounts


def remove_account(email: str) -> None:
    """Deletes this account's saved credentials - the wizard's
    "disconnect" action. No-op if nothing was saved for this email."""
    _account_path(email).unlink(missing_ok=True)


def save_credentials(email: str, app_password: str, host: str, port: int = 993) -> None:
    """Stores this account's IMAP login, encrypted at rest. Called by the
    wizard after email_providers.detect_imap_settings() has resolved (or
    the user has manually confirmed) host/port."""
    IMAP_DIR.mkdir(parents=True, exist_ok=True)
    write_json(_account_path(email), {
        "email": email,
        "app_password": app_password,
        "host": host,
        "port": port,
    })


def _load_credentials(email: str) -> dict:
    creds = read_json(_account_path(email), default=None)
    if creds is None:
        raise ImapNotConfigured(
            f"No IMAP credentials saved for {email} - run the account-setup "
            "wizard's 'Other email' step first."
        )
    return creds


def _connect(email: str) -> imaplib.IMAP4_SSL:
    creds = _load_credentials(email)
    try:
        conn = imaplib.IMAP4_SSL(creds["host"], creds["port"], timeout=_CONNECT_TIMEOUT)
        conn.login(creds["email"], creds["app_password"])
    except (imaplib.IMAP4.error, OSError) as exc:
        raise ImapConnectionError(f"Couldn't connect/log in to {creds['host']}: {exc}") from exc
    return conn


@dataclass
class MessageSummary:
    uid: str
    subject: str
    sender: str
    date: str
    snippet: str


def _decode_header_value(raw: Optional[str]) -> str:
    if not raw:
        return ""
    parts = email_lib.header.decode_header(raw)
    decoded = []
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded)


def _walk_plain_text(msg) -> str:
    """Prefers the first text/plain part, falls back to text/html with
    tags/style/script stripped - same crude-but-adequate approach
    gmail_client.py's _decode_body uses, for the same reason (read for
    classification, not rendered)."""
    if msg.is_multipart():
        plain, html = None, None
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain" and plain is None:
                plain = part.get_payload(decode=True)
            elif content_type == "text/html" and html is None:
                html = part.get_payload(decode=True)
        if plain is not None:
            return plain.decode(errors="replace")
        if html is not None:
            html_text = html.decode(errors="replace")
            html_text = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", " ", html_text, flags=re.IGNORECASE | re.DOTALL)
            return re.sub(r"<[^>]+>", " ", html_text)
        return ""
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    text = payload.decode(errors="replace")
    if msg.get_content_type() == "text/html":
        text = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
    return text


def search_messages(email: str, criteria: str = "UNSEEN", folder: str = "INBOX", max_results: int = 50) -> list[MessageSummary]:
    """IMAP SEARCH in `folder` (default the standard IMAP criteria syntax,
    e.g. "UNSEEN", 'SINCE "01-Aug-2026"'), newest first, capped at
    min(max_results, _MAX_RESULTS)."""
    conn = _connect(email)
    try:
        conn.select(folder, readonly=True)
        status, data = conn.search(None, criteria)
        if status != "OK":
            raise ImapConnectionError(f"IMAP SEARCH failed: {status}")
        uids = data[0].split()
        uids = uids[-min(max_results, _MAX_RESULTS):]
        uids.reverse()  # newest first

        summaries = []
        for uid in uids:
            status, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            header_text = msg_data[0][1]
            parsed = email_lib.message_from_bytes(header_text)
            summaries.append(MessageSummary(
                uid=uid.decode(),
                subject=_decode_header_value(parsed.get("Subject")) or "(no subject)",
                sender=_decode_header_value(parsed.get("From")) or "",
                date=parsed.get("Date") or "",
                snippet="",  # generic IMAP has no server-side snippet like Gmail's -
                # callers needing a body preview should call get_message().
            ))
        return summaries
    finally:
        conn.logout()


def get_message(email: str, uid: str, folder: str = "INBOX") -> dict:
    conn = _connect(email)
    try:
        conn.select(folder, readonly=True)
        status, msg_data = conn.fetch(uid.encode() if isinstance(uid, str) else uid, "(RFC822)")
        if status != "OK" or not msg_data or msg_data[0] is None:
            raise ImapConnectionError(f"Couldn't fetch message {uid}")
        raw = msg_data[0][1]
        parsed = email_lib.message_from_bytes(raw)
        return {
            "uid": uid,
            "subject": _decode_header_value(parsed.get("Subject")) or "(no subject)",
            "sender": _decode_header_value(parsed.get("From")) or "",
            "date": parsed.get("Date") or "",
            "body": _walk_plain_text(parsed),
        }
    finally:
        conn.logout()


def list_folders(email: str) -> list[str]:
    conn = _connect(email)
    try:
        status, data = conn.list()
        if status != "OK":
            raise ImapConnectionError(f"IMAP LIST failed: {status}")
        names = []
        for line in data:
            # RFC 3501 mailbox-list format: (\Flags) "delim" "Name" - the
            # quoted name is the last quoted token; unquoted names (rare,
            # some servers omit quotes for simple names) fall back to the
            # last whitespace-separated token.
            decoded = line.decode(errors="replace")
            match = re.search(r'"([^"]*)"$', decoded)
            names.append(match.group(1) if match else decoded.split()[-1])
        return names
    finally:
        conn.logout()


def ensure_folder(email: str, name: str) -> None:
    """Creates `name` if it doesn't already exist - the closest IMAP analog
    to gmail_client.py's ensure_label(). IMAP CREATE on an existing folder
    is an error on some servers, so check first rather than rely on a
    try/except-and-ignore."""
    if name in list_folders(email):
        return
    conn = _connect(email)
    try:
        status, _ = conn.create(name)
        if status != "OK":
            raise ImapConnectionError(f"Couldn't create folder {name!r}")
    finally:
        conn.logout()


def move_to_folder(email: str, uid: str, dest_folder: str, source_folder: str = "INBOX") -> None:
    """Closest IMAP analog to gmail_client.py's label_thread/archive
    behavior: moves the message out of source_folder into dest_folder
    (COPY + delete-from-source, since not every server supports the IMAP
    MOVE extension)."""
    conn = _connect(email)
    try:
        conn.select(source_folder)
        uid_bytes = uid.encode() if isinstance(uid, str) else uid
        status, _ = conn.copy(uid_bytes, dest_folder)
        if status != "OK":
            raise ImapConnectionError(f"Couldn't copy message {uid} to {dest_folder!r}")
        conn.store(uid_bytes, "+FLAGS", "\\Deleted")
        conn.expunge()
    finally:
        conn.logout()


def create_draft(email: str, to: str, subject: str, body: str, in_reply_to: Optional[str] = None) -> str:
    """Appends a real draft message straight into the Drafts folder with
    the \\Draft flag set - there is no send path in this module at all, by
    construction (see module docstring). `in_reply_to` is the raw
    Message-ID header value of the message being replied to (not a UID -
    generic IMAP UIDs aren't stable across folders, but Message-ID is), so
    the draft threads correctly in the recipient's own mail client.
    Returns the new draft's UID (best-effort - some servers don't return
    an APPENDUID response code, in which case this returns "" rather than
    guessing)."""
    _, address = parseaddr(to)
    address = address or to

    message = MIMEText(body)
    message["To"] = address
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to

    conn = _connect(email)
    try:
        status, response = conn.append("Drafts", "\\Draft", None, message.as_bytes())
        if status != "OK":
            raise ImapConnectionError(f"Couldn't save draft: {response}")
        # Best-effort UID extraction from an APPENDUID response code, e.g.
        # b'[APPENDUID 1 123] APPEND completed' - not guaranteed present.
        if response and response[0]:
            match = re.search(rb"APPENDUID \d+ (\d+)", response[0])
            if match:
                return match.group(1).decode()
        return ""
    finally:
        conn.logout()


def list_drafts(email: str) -> list[str]:
    """UIDs of everything currently in the Drafts folder, for the
    fulfillment loop's reconciliation step (same purpose as
    gmail_client.py's list_drafts)."""
    conn = _connect(email)
    try:
        conn.select("Drafts", readonly=True)
        status, data = conn.search(None, "ALL")
        if status != "OK":
            raise ImapConnectionError(f"IMAP SEARCH failed: {status}")
        return [uid.decode() for uid in data[0].split()[:_MAX_RESULTS]]
    finally:
        conn.logout()
