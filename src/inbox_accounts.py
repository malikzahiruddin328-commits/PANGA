"""Unified adapter over every configured inbox provider - Gmail,
Outlook/Hotmail, and any number of Yahoo/other IMAP accounts - so
scripts/gmail_cta_scan.py and scripts/cta_fulfillment.py can loop over
"every configured account" instead of being Gmail-only (see
docs/multi-provider-accounts-scope.md). Connecting an account through the
Settings wizard is what makes it show up here automatically - nothing
else needs to be configured.

No MCP anywhere in this module or anything it calls - every provider goes
through its own direct-API/OAuth/IMAP client (gmail_client.py,
microsoft_client.py, imap_client.py), the same "no live session = no MCP"
property gmail_client.py was originally built around (confirmed zero
"mailopoly" references anywhere in src/ as of this module's addition).

Each adapter exposes the same 6 operations the scan/fulfillment scripts
actually need - list_recent_unreviewed(), get_body(), mark_reviewed(),
mark_cta(), archive(), create_reply_draft() - backed by whichever real
client that account uses:
  - Gmail: labels (thread-level), gmail_client.py.
  - Microsoft: categories (message-level - Graph has no thread-level
    category API), microsoft_client.py.
  - IMAP: a custom "PangaReviewed"/"PangaCTA" keyword flag (message-level,
    non-destructive - leaves the message in INBOX, same as a Gmail
    label), imap_client.py. Not every IMAP server accepts custom
    keywords; a server that rejects one is treated as best-effort (logged
    and skipped, not fatal to the whole run - see IMAPAccount's methods).

"New, not yet reviewed" filtering is client-side and uniform across all
three: fetch everything from the last _LOOKBACK_DAYS days, then check
whether the reviewed marker is already present in Python. Gmail's own
query language CAN express "not yet labeled" server-side more
efficiently, and still does (search_threads' query string already
excludes the reviewed label) - but Outlook's collection-property $filter
negation is unreliable across Graph API versions, and composing IMAP
SEARCH's KEYWORD/NOT correctly is easy to get subtly wrong, so this
module normalizes on the same simple, easy-to-verify client-side check
for those two rather than trust two different, harder-to-verify
server-side query paths. At personal-inbox scale (a few dozen messages
over _LOOKBACK_DAYS), re-fetching a small bounded window every run is a
non-issue (see CLAUDE.md's "design with performance in mind" - this is
bounded, not O(n) over the whole mailbox).

Sequential, not concurrent: the calling scripts loop over configured_accounts()
one at a time, never in parallel. The only shared-state surface this adds
is more callers writing to cta_emails.json/applications.json, which
already have their own locked() critical sections (security/file_lock.py)
regardless of how many accounts write to them - staying sequential here
just means this module never introduces any NEW concurrency on top of
that existing protection.
"""

from dataclasses import dataclass
from typing import Optional, Protocol

import gmail_client
import imap_client
import microsoft_client

REVIEWED_LABEL = "Panga/Reviewed"
CTA_LABEL = "Panga/Call-to-Action"
HANDLED_LABEL = "Panga/Handled"
IMAP_REVIEWED_KEYWORD = "PangaReviewed"
IMAP_CTA_KEYWORD = "PangaCTA"

_LOOKBACK_DAYS = 2  # matches gmail_cta_scan.py's original "newer_than:2d"
_MAX_IMAP_ACCOUNTS = 25  # sanity bound on account discovery - a runaway
                          # credentials directory must not turn "loop over
                          # every configured account" into an unbounded loop.


@dataclass
class InboxMessage:
    provider: str   # "gmail" | "microsoft" | "imap"
    account: str    # "gmail" | "Outlook" | the IMAP account's email address
    ref: str        # the value stored as cta_emails.json's thread_id - thread_id
    # (gmail) / conversation_id (microsoft) / uid (imap)
    message_id: Optional[str]  # stored as cta_emails.json's message_id - used
    # later for reply threading (see each adapter's create_reply_draft)
    subject: str
    sender: str
    date: str
    snippet: str


class InboxAccount(Protocol):
    provider: str
    account: str

    def list_recent_unreviewed(self) -> list[InboxMessage]: ...
    def get_body(self, ref: str) -> str: ...
    def mark_reviewed(self, ref: str) -> None: ...
    def mark_cta(self, ref: str) -> None: ...
    def archive(self, ref: str) -> None: ...
    def web_link(self, ref: str) -> Optional[str]: ...
    def create_reply_draft(self, to: str, subject: str, body: str, reply_to: Optional[str]) -> tuple[str, Optional[str]]: ...
    def list_current_draft_ids(self) -> list[str]: ...


class GmailAccount:
    provider = "gmail"
    account = "gmail"

    def __init__(self):
        self._reviewed_id = None
        self._cta_id = None
        self._handled_id = None

    def _label_ids(self) -> tuple[str, str, str]:
        if self._reviewed_id is None:
            self._reviewed_id = gmail_client.ensure_label(REVIEWED_LABEL)
            self._cta_id = gmail_client.ensure_label(CTA_LABEL)
            self._handled_id = gmail_client.ensure_label(HANDLED_LABEL)
        return self._reviewed_id, self._cta_id, self._handled_id

    def list_recent_unreviewed(self) -> list[InboxMessage]:
        query = f"-label:{REVIEWED_LABEL} -in:spam -in:trash newer_than:{_LOOKBACK_DAYS}d in:inbox"
        threads = gmail_client.search_threads(query)
        return [
            InboxMessage(self.provider, self.account, t["thread_id"], t.get("message_id"),
                         t["subject"], t["sender"], t["date"], t["snippet"])
            for t in threads
        ]

    def get_body(self, ref: str) -> str:
        thread = gmail_client.get_thread(ref)
        return "\n\n".join(m["body"] for m in thread["messages"] if m["body"])

    def mark_reviewed(self, ref: str) -> None:
        reviewed_id, _, _ = self._label_ids()
        gmail_client.label_thread(ref, [reviewed_id])

    def mark_cta(self, ref: str) -> None:
        reviewed_id, cta_id, _ = self._label_ids()
        gmail_client.label_thread(ref, [reviewed_id, cta_id])

    def archive(self, ref: str) -> None:
        _, _, handled_id = self._label_ids()
        gmail_client.unlabel_thread(ref, ["INBOX"])
        gmail_client.label_thread(ref, [handled_id])

    def web_link(self, ref: str) -> Optional[str]:
        return f"https://mail.google.com/mail/u/0/#all/{ref}"

    def create_reply_draft(self, to: str, subject: str, body: str, reply_to: Optional[str]) -> tuple[str, Optional[str]]:
        draft_id = gmail_client.create_draft(to=to, subject=subject, body=body, reply_to_message_id=reply_to)
        return draft_id, f"https://mail.google.com/mail/u/0/#drafts?compose={draft_id}"

    def list_current_draft_ids(self) -> list[str]:
        return gmail_client.list_drafts()


class MicrosoftAccount:
    provider = "microsoft"
    account = "Outlook"

    def __init__(self):
        self._reviewed_registered = False

    def _ensure_categories(self) -> None:
        # Registering as a masterCategory isn't required for label_thread
        # to work (see microsoft_client.ensure_label's own docstring) -
        # this is just so it shows up with a real color in Outlook's own
        # UI, done once per run rather than once per message.
        if not self._reviewed_registered:
            microsoft_client.ensure_label(REVIEWED_LABEL)
            microsoft_client.ensure_label(CTA_LABEL)
            self._reviewed_registered = True

    def list_recent_unreviewed(self) -> list[InboxMessage]:
        recent = microsoft_client.list_recent(_LOOKBACK_DAYS)
        return [
            InboxMessage(self.provider, self.account, m["message_id"], m["message_id"],
                         m["subject"], m["sender"], m["date"], m["snippet"])
            for m in recent
            if REVIEWED_LABEL not in (m.get("categories") or [])
        ]

    def get_body(self, ref: str) -> str:
        return microsoft_client.get_message_body(ref)

    def mark_reviewed(self, ref: str) -> None:
        self._ensure_categories()
        microsoft_client.label_thread(ref, [REVIEWED_LABEL])

    def mark_cta(self, ref: str) -> None:
        self._ensure_categories()
        microsoft_client.label_thread(ref, [REVIEWED_LABEL, CTA_LABEL])

    def archive(self, ref: str) -> None:
        self._ensure_categories()
        microsoft_client.label_thread(ref, [HANDLED_LABEL])

    def web_link(self, ref: str) -> Optional[str]:
        return None  # no reliable Outlook webmail deep link for a specific message/conversation

    def create_reply_draft(self, to: str, subject: str, body: str, reply_to: Optional[str]) -> tuple[str, Optional[str]]:
        draft_id = microsoft_client.create_draft(to, subject, body, reply_to_message_id=reply_to)
        return draft_id, None  # same "no reliable deep link" limit as web_link

    def list_current_draft_ids(self) -> list[str]:
        return microsoft_client.list_drafts()


class IMAPAccount:
    provider = "imap"

    def __init__(self, email: str):
        self.account = email
        self._email = email

    def list_recent_unreviewed(self) -> list[InboxMessage]:
        recent = imap_client.search_recent(self._email, since_days=_LOOKBACK_DAYS)
        return [
            InboxMessage(self.provider, self.account, m.uid, m.message_id_header,
                         m.subject, m.sender, m.date, m.snippet)
            for m in recent
            if IMAP_REVIEWED_KEYWORD not in m.flags
        ]

    def get_body(self, ref: str) -> str:
        return imap_client.get_message(self._email, ref)["body"]

    def mark_reviewed(self, ref: str) -> None:
        imap_client.add_keyword(self._email, ref, IMAP_REVIEWED_KEYWORD)

    def mark_cta(self, ref: str) -> None:
        imap_client.add_keyword(self._email, ref, IMAP_REVIEWED_KEYWORD)
        imap_client.add_keyword(self._email, ref, IMAP_CTA_KEYWORD)

    def archive(self, ref: str) -> None:
        # A real folder-move (unlike Gmail/Outlook's "mark handled but
        # leave in inbox") - IMAP has no separate "archived but still in
        # inbox" concept the way Gmail labels do, so this mirrors the
        # original "remove from inbox" half of Gmail's archive behavior
        # using the closest IMAP primitive available.
        imap_client.ensure_folder(self._email, "Panga.Handled")
        imap_client.move_to_folder(self._email, ref, "Panga.Handled")

    def web_link(self, ref: str) -> Optional[str]:
        return None  # no generic IMAP webmail deep-link convention

    def create_reply_draft(self, to: str, subject: str, body: str, reply_to: Optional[str]) -> tuple[str, Optional[str]]:
        draft_uid = imap_client.create_draft(self._email, to, subject, body, in_reply_to=reply_to)
        return draft_uid, None  # same "no reliable deep link" limit as web_link

    def list_current_draft_ids(self) -> list[str]:
        return imap_client.list_drafts(self._email)


def configured_accounts() -> list:
    """Every account the wizard has connected, in a fixed, deterministic
    order (Gmail, then Microsoft, then IMAP accounts alphabetically) - not
    parallelized, not randomized, so a run's logs/notifications read the
    same way run to run. Bounded IMAP account count (see
    _MAX_IMAP_ACCOUNTS module constant) - account discovery reads a real
    directory listing, but this is defense against a corrupted/runaway
    data/imap/ directory turning discovery into an unbounded loop, not an
    expected real-world limit."""
    accounts = []
    if gmail_client.is_configured():
        accounts.append(GmailAccount())
    if microsoft_client.is_configured():
        accounts.append(MicrosoftAccount())
    for email in sorted(imap_client.list_configured_accounts())[:_MAX_IMAP_ACCOUNTS]:
        accounts.append(IMAPAccount(email))
    return accounts
