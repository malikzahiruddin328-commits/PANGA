"""Tests for imap_client.py. Pure-logic parsing helpers are tested
directly; the network-touching functions (search_messages, get_message,
move_to_folder, create_draft, list_folders) are tested against a small
FakeImap stand-in for imaplib.IMAP4_SSL (monkeypatched in), verifying the
actual protocol sequence (SELECT/SEARCH/FETCH/APPEND/COPY+STORE+EXPUNGE)
rather than a real network round-trip - no live IMAP server was reachable
to verify against from this environment (flagged, same pattern as
email_providers.py's ISPDB tests)."""

import email as email_lib
from email.mime.text import MIMEText

import pytest

import imap_client


def test_decode_header_value_plain():
    assert imap_client._decode_header_value("Hello there") == "Hello there"


def test_decode_header_value_mime_encoded():
    # A real MIME encoded-word header, as many IMAP servers send non-ASCII
    # subjects/names.
    encoded = "=?UTF-8?B?SGVsbG8gd29ybGQ=?="  # "Hello world" base64-encoded
    assert imap_client._decode_header_value(encoded) == "Hello world"


def test_decode_header_value_none():
    assert imap_client._decode_header_value(None) == ""


def test_walk_plain_text_simple_message():
    msg = MIMEText("Just a plain body.")
    parsed = email_lib.message_from_bytes(msg.as_bytes())
    assert imap_client._walk_plain_text(parsed) == "Just a plain body."


def test_walk_plain_text_strips_html_style_and_script():
    html = "<html><head><style>.x{color:red}</style></head><body>Real <b>text</b> here</body></html>"
    msg = MIMEText(html, "html")
    parsed = email_lib.message_from_bytes(msg.as_bytes())
    result = imap_client._walk_plain_text(parsed)
    assert "color:red" not in result
    assert "Real" in result and "text" in result


def test_account_path_sanitizes_email():
    path = imap_client._account_path("Someone.Weird+tag@Example.COM")
    assert path.name == "someone.weird_tag@example.com.json"


class FakeImap:
    """Minimal imaplib.IMAP4_SSL stand-in - just enough surface for
    imap_client.py's calls, recording what was invoked so tests can assert
    on the actual protocol sequence."""

    def __init__(self, *a, **kw):
        self.calls = []
        self.appended = None

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"1"]

    def search(self, charset, criteria):
        self.calls.append(("search", criteria))
        if criteria == "ALL":
            return "OK", [b"5 6 7"]
        return "OK", [b"10 11 12"]

    def fetch(self, uid, spec):
        self.calls.append(("fetch", uid, spec))
        if b"HEADER.FIELDS" in spec.encode():
            msg = MIMEText("body")
            msg["Subject"] = "Test subject"
            msg["From"] = "sender@example.com"
            msg["Date"] = "Mon, 03 Aug 2026 10:00:00 -0000"
            return "OK", [(b"1", msg.as_bytes())]
        msg = MIMEText("Full body text")
        msg["Subject"] = "Test subject"
        msg["From"] = "sender@example.com"
        return "OK", [(b"1", msg.as_bytes())]

    def copy(self, uid, dest):
        self.calls.append(("copy", uid, dest))
        return "OK", [b"copied"]

    def store(self, uid, flag_op, flags):
        self.calls.append(("store", uid, flag_op, flags))
        return "OK", [b""]

    def expunge(self):
        self.calls.append(("expunge",))
        return "OK", [b""]

    def append(self, folder, flags, date, message_bytes):
        self.calls.append(("append", folder, flags))
        self.appended = message_bytes
        return "OK", [b"[APPENDUID 1 42] APPEND completed"]

    def list(self):
        self.calls.append(("list",))
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Drafts"']

    def logout(self):
        self.calls.append(("logout",))


@pytest.fixture
def fake_connect(monkeypatch):
    fake = FakeImap()
    monkeypatch.setattr(imap_client, "_connect", lambda email: fake)
    return fake


def test_search_messages_returns_summaries(fake_connect):
    results = imap_client.search_messages("me@example.com", criteria="UNSEEN")
    assert len(results) == 3
    assert results[0].subject == "Test subject"
    assert results[0].sender == "sender@example.com"
    # newest-first: the fake returns uids 10 11 12, reversed
    assert [r.uid for r in results] == ["12", "11", "10"]


def test_get_message_full_body(fake_connect):
    result = imap_client.get_message("me@example.com", "10")
    assert result["subject"] == "Test subject"
    assert "Full body text" in result["body"]


def test_move_to_folder_copies_flags_and_expunges(fake_connect):
    imap_client.move_to_folder("me@example.com", "10", "Panga/Reviewed")
    kinds = [c[0] for c in fake_connect.calls]
    assert kinds == ["select", "copy", "store", "expunge", "logout"]
    assert fake_connect.calls[2][2] == "+FLAGS"
    assert fake_connect.calls[2][3] == "\\Deleted"


def test_create_draft_sets_draft_flag_and_returns_uid(fake_connect):
    draft_uid = imap_client.create_draft("me@example.com", "them@example.com", "Subj", "Body text")
    assert draft_uid == "42"
    append_call = [c for c in fake_connect.calls if c[0] == "append"][0]
    assert append_call[1] == "Drafts"
    assert append_call[2] == "\\Draft"
    assert b"Body text" in fake_connect.appended


def test_create_draft_threads_reply(fake_connect):
    imap_client.create_draft("me@example.com", "them@example.com", "Re: Subj", "Body", in_reply_to="<abc123@example.com>")
    assert b"In-Reply-To: <abc123@example.com>" in fake_connect.appended


def test_list_folders_parses_quoted_names(fake_connect):
    assert imap_client.list_folders("me@example.com") == ["INBOX", "Drafts"]
