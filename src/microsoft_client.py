"""Microsoft Graph API client for Outlook/Hotmail (native-packaging's
account-setup wizard - see docs/multi-provider-accounts-scope.md Part A).
Outlook and Hotmail are the same Microsoft account/OAuth backend under the
hood (Microsoft folded Hotmail into Outlook.com years ago) - this module
serves both; the wizard just needs to show them as two separate picker
labels (see the scope doc), both pointing here.

OAuth model, mirroring gmail_client.py: Zahir creates his own Azure AD app
registration once (a public-client "Desktop" app, personal Microsoft
accounts only - the "consumers" tenant, not "organizations"/work
accounts), saves the client id to data/microsoft/client_id.txt - no client
secret needed (public clients use PKCE, not a secret). The first real call
opens a browser for consent (msal's acquire_token_interactive, a
local-server flow analogous to google_auth_oauthlib's
run_local_server); the resulting token cache is saved at
data/microsoft/token_cache.bin (encrypted via security.crypto_store, same
treatment gmail_client.py gives its own token.json) and silently
refreshed after that.

Graph's mail model differs from Gmail's in two ways worth knowing before
extending this:
- No server-side "thread" the way Gmail has - Graph groups messages by
  conversationId instead. Close enough for our purposes, used the same way.
- No "label" concept - Graph uses "categories", a plain string array on
  each message, and category actions are per-message (Graph has no
  thread-level category endpoint), unlike Gmail's per-thread label calls.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import msal
import requests

from security.crypto_store import read_text, write_text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT_ID_PATH = PROJECT_ROOT / "data" / "microsoft" / "client_id.txt"
TOKEN_CACHE_PATH = PROJECT_ROOT / "data" / "microsoft" / "token_cache.bin"

AUTHORITY = "https://login.microsoftonline.com/consumers"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Mail.ReadWrite covers reading, categorizing (label analog), moving, and
# creating/editing drafts. Deliberately NOT requesting Mail.Send - matches
# gmail_client.py's "read/label/draft, never send automatically" property.
SCOPES = ["Mail.ReadWrite"]

_MAX_PAGES = 20
_REQUEST_TIMEOUT = 15


class MicrosoftNotConfigured(Exception):
    pass


class MicrosoftApiError(Exception):
    pass


def is_configured() -> bool:
    return CLIENT_ID_PATH.exists()


def save_client_id(client_id: str) -> None:
    CLIENT_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_ID_PATH.write_text(client_id.strip())


def _load_client_id() -> str:
    if not is_configured():
        raise MicrosoftNotConfigured(
            "No Microsoft app registration found. In Azure Portal > App "
            "registrations, create a new registration (supported account "
            "types: personal Microsoft accounts only), add a "
            "'Mobile and desktop applications' platform with redirect URI "
            "http://localhost, then save its Application (client) ID to "
            f"{CLIENT_ID_PATH}."
        )
    return CLIENT_ID_PATH.read_text().strip()


def _load_token_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    cached = read_text(TOKEN_CACHE_PATH, default=None)
    if cached is not None:
        cache.deserialize(cached)
    return cache


def _save_token_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_text(TOKEN_CACHE_PATH, cache.serialize())


def get_access_token() -> str:
    """Returns a valid access token, refreshing silently or running the
    one-time interactive consent flow as needed - same shape as
    gmail_client.py's get_credentials()."""
    cache = _load_token_cache()
    app = msal.PublicClientApplication(_load_client_id(), authority=AUTHORITY, token_cache=cache)

    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if result is None:
        # Generous timeout - a real person clicking through Microsoft's
        # consent screen, same reasoning as gmail_client.py's
        # run_local_server timeout_seconds=300.
        result = app.acquire_token_interactive(scopes=SCOPES, timeout=300)

    _save_token_cache(cache)

    if not result or "access_token" not in result:
        error = (result or {}).get("error_description") or (result or {}).get("error") or "no result"
        raise MicrosoftNotConfigured(f"Microsoft sign-in failed: {error}")
    return result["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}", "Content-Type": "application/json"}


def _get(path: str, params: Optional[dict] = None) -> dict:
    resp = requests.get(f"{GRAPH_BASE}{path}", headers=_headers(), params=params, timeout=_REQUEST_TIMEOUT)
    if not resp.ok:
        raise MicrosoftApiError(f"Graph GET {path} failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _post(path: str, body: dict) -> dict:
    resp = requests.post(f"{GRAPH_BASE}{path}", headers=_headers(), json=body, timeout=_REQUEST_TIMEOUT)
    if not resp.ok:
        raise MicrosoftApiError(f"Graph POST {path} failed ({resp.status_code}): {resp.text}")
    return resp.json() if resp.text else {}


def _patch(path: str, body: dict) -> None:
    resp = requests.patch(f"{GRAPH_BASE}{path}", headers=_headers(), json=body, timeout=_REQUEST_TIMEOUT)
    if not resp.ok:
        raise MicrosoftApiError(f"Graph PATCH {path} failed ({resp.status_code}): {resp.text}")


def _strip_html(html: str) -> str:
    html = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    return re.sub(r"<[^>]+>", " ", html)


def _message_summary(msg: dict) -> dict:
    return {
        "message_id": msg.get("id"),
        "conversation_id": msg.get("conversationId"),
        "subject": msg.get("subject") or "(no subject)",
        "sender": (msg.get("from", {}).get("emailAddress", {}) or {}).get("address", ""),
        "date": msg.get("receivedDateTime", ""),
        "snippet": msg.get("bodyPreview", ""),
    }


def search_threads(query: str, max_results: int = 50) -> list[dict]:
    """Mirrors gmail_client.py's search_threads: one summary per
    conversation (Graph's thread analog), newest first, up to
    max_results. `query` is a plain free-text string (Graph's $search is
    much simpler than Gmail's query language - no -label:/in: operators)."""
    data = _get("/me/messages", params={
        "$search": f'"{query}"',
        "$top": min(max_results, 50),
        "$orderby": "receivedDateTime desc",
    })
    seen_conversations = set()
    summaries = []
    for msg in data.get("value", []):
        conv_id = msg.get("conversationId")
        if conv_id in seen_conversations:
            continue
        seen_conversations.add(conv_id)
        summaries.append(_message_summary(msg))
        if len(summaries) >= max_results:
            break
    return summaries


def list_recent(since_days: int, max_results: int = 100) -> list[dict]:
    """Messages received in the last `since_days` days, one summary per
    conversation (deduped like search_threads), each including its current
    `categories` list so callers can filter by "not yet reviewed"
    client-side (see inbox_accounts.py). Graph's $filter can't reliably
    express "does NOT have this category" for a multi-value property
    across API versions, so this fetches by date only and lets the caller
    check categories in Python instead of fighting the query language for
    the negation half."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _get("/me/mailFolders/inbox/messages", params={
        "$filter": f"receivedDateTime ge {cutoff}",
        "$top": min(max_results, 50),
        "$orderby": "receivedDateTime desc",
        "$select": "id,conversationId,subject,from,receivedDateTime,bodyPreview,categories",
    })
    seen_conversations = set()
    summaries = []
    for msg in data.get("value", []):
        conv_id = msg.get("conversationId")
        if conv_id in seen_conversations:
            continue
        seen_conversations.add(conv_id)
        summary = _message_summary(msg)
        summary["categories"] = msg.get("categories", [])
        summaries.append(summary)
        if len(summaries) >= max_results:
            break
    return summaries


def get_message_body(message_id: str) -> str:
    """Full plain-text body of one specific message (not a whole
    conversation) - used by inbox_accounts.py, which already knows the
    message id from list_recent() and doesn't need get_thread()'s
    full-conversation refetch just to read one message."""
    msg = _get(f"/me/messages/{message_id}", params={"$select": "body"})
    body = msg.get("body", {}) or {}
    content = body.get("content", "")
    if body.get("contentType") == "html":
        content = _strip_html(content)
    return content


def get_thread(conversation_id: str) -> dict:
    """All messages in this conversation, oldest first - Graph doesn't
    guarantee ordering on a conversationId filter, so this sorts
    explicitly rather than trusting server order."""
    data = _get("/me/messages", params={
        "$filter": f"conversationId eq '{conversation_id}'",
        "$top": 50,
    })
    messages = sorted(data.get("value", []), key=lambda m: m.get("receivedDateTime", ""))
    result_messages = []
    for m in messages:
        body = m.get("body", {}) or {}
        content = body.get("content", "")
        if body.get("contentType") == "html":
            content = _strip_html(content)
        result_messages.append({
            "message_id": m.get("id"),
            "subject": m.get("subject") or "(no subject)",
            "sender": (m.get("from", {}).get("emailAddress", {}) or {}).get("address", ""),
            "date": m.get("receivedDateTime", ""),
            "snippet": m.get("bodyPreview", ""),
            "body": content,
        })
    return {"thread_id": conversation_id, "messages": result_messages}


def list_labels() -> list[dict]:
    """Categories, Graph's label analog - see module docstring. Surfaces
    the mailbox's currently-defined categories, for parity with
    gmail_client.py's list_labels."""
    data = _get("/me/outlook/masterCategories")
    return [{"id": c["id"], "name": c["displayName"]} for c in data.get("value", [])]


def ensure_label(name: str) -> str:
    """Registers `name` as a defined category if it isn't already one -
    same "check with list_labels, create if missing" pattern as
    gmail_client.py, even though (unlike Gmail) assigning an unregistered
    category name to a message would technically still work without this -
    kept for parity and so it shows up with a real color in Outlook's own
    UI, not just as an ad hoc string."""
    for label in list_labels():
        if label["name"] == name:
            return label["id"]
    created = _post("/me/outlook/masterCategories", {"displayName": name, "color": "preset0"})
    return created["id"]


def label_thread(message_id: str, label_names: list[str]) -> None:
    """Adds categories to one message. Graph has no thread-level category
    API - unlike Gmail's label_thread, this operates per-message; a
    caller wanting every message in a conversation labeled should call
    this once per message_id."""
    current = _get(f"/me/messages/{message_id}", params={"$select": "categories"})
    merged = sorted(set(current.get("categories", [])) | set(label_names))
    _patch(f"/me/messages/{message_id}", {"categories": merged})


def unlabel_thread(message_id: str, label_names: list[str]) -> None:
    current = _get(f"/me/messages/{message_id}", params={"$select": "categories"})
    remaining = sorted(set(current.get("categories", [])) - set(label_names))
    _patch(f"/me/messages/{message_id}", {"categories": remaining})


def create_draft(to: str, subject: str, body: str, reply_to_message_id: Optional[str] = None) -> str:
    """Creates a real Outlook draft (never sent - Mail.Send is never in
    SCOPES, see module docstring). When reply_to_message_id is given, uses
    Graph's own createReply action so the draft threads onto that message
    natively, then overwrites its auto-quoted body/recipient with the
    drafted text - same "plain reply, not quoted-and-blended" intent as
    gmail_client.py's MIMEText-from-scratch approach."""
    if reply_to_message_id:
        draft = _post(f"/me/messages/{reply_to_message_id}/createReply", {})
        _patch(f"/me/messages/{draft['id']}", {
            "toRecipients": [{"emailAddress": {"address": to}}],
            "body": {"contentType": "text", "content": body},
        })
        return draft["id"]

    # POSTing to /me/messages creates a draft by default - Graph rejects
    # an explicit "isDraft" field in the body (it's a read-only computed
    # property on the message resource, not something a caller sets).
    draft = _post("/me/messages", {
        "subject": subject,
        "body": {"contentType": "text", "content": body},
        "toRecipients": [{"emailAddress": {"address": to}}],
    })
    return draft["id"]


def list_drafts() -> list[str]:
    """Message ids currently in the Drafts folder, same purpose as
    gmail_client.py's list_drafts. Paginated via Graph's @odata.nextLink,
    bounded to _MAX_PAGES for the same reason gmail_client.py bounds its
    own pagination loop."""
    ids: list[str] = []
    path = "/me/mailFolders/drafts/messages"
    params = {"$select": "id", "$top": 50}
    for _ in range(_MAX_PAGES):
        data = _get(path, params=params)
        ids.extend(m["id"] for m in data.get("value", []))
        next_link = data.get("@odata.nextLink")
        if not next_link:
            break
        path = next_link[len(GRAPH_BASE):]
        params = None
    return ids
