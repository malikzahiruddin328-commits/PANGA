"""Tests for imap_client.py. Pure-logic parsing helpers are tested
directly; the network-touching functions are tested against a small
FakeImap stand-in for imaplib.IMAP4_SSL (monkeypatched in), verifying the
actual protocol sequence - in particular that every SEARCH/FETCH/COPY/
STORE call goes through conn.uid(...), not the plain sequence-number
variant (see the module docstring's note on the bug this replaced) - no
live IMAP server was reachable to verify against from this environment."""

import email as email_lib
from email.mime.text import MIMEText

import pytest

import imap_client


def test_decode_header_value_plain():
    assert imap_client._decode_header_value("Hello there") == "Hello there"


def test_decode_header_value_mime_encoded():
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


def test_parse_flags_line():
    uid, flags = imap_client._parse_flags_line(rb"3 (UID 12 FLAGS (\Seen PangaReviewed))")
    assert uid == "12"
    assert flags == {"\\Seen", "PangaReviewed"}


def test_parse_flags_line_no_flags():
    uid, flags = imap_client._parse_flags_line(rb"3 (UID 12 FLAGS ())")
    assert uid == "12"
    assert flags == set()


def test_parse_flags_line_unmatched():
    uid, flags = imap_client._parse_flags_line(b"not a fetch response")
    assert uid is None
    assert flags == set()


class FakeImap:
    """Minimal imaplib.IMAP4_SSL stand-in - just enough surface for
    imap_client.py's calls, recording what was invoked so tests can assert
    on the actual protocol sequence, including that UID variants are used
    throughout (a plain, non-UID conn.search()/.fetch()/.copy()/.store()
    call landing here would be a regression back to the sequence-number
    bug this module was fixed to avoid)."""

    def __init__(self, *a, **kw):
        self.calls = []
        self.appended = None
        self.flags_by_uid = {b"10": set(), b"11": set(), b"12": {"PangaReviewed"}}

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"1"]

    def search(self, *a, **kw):  # pragma: no cover - must never be called; see uid()
        raise AssertionError("search() (non-UID) must not be called - use uid('search', ...)")

    def fetch(self, *a, **kw):  # pragma: no cover
        raise AssertionError("fetch() (non-UID) must not be called - use uid('fetch', ...)")

    def copy(self, *a, **kw):  # pragma: no cover
        raise AssertionError("copy() (non-UID) must not be called - use uid('copy', ...)")

    def store(self, *a, **kw):  # pragma: no cover
        raise AssertionError("store() (non-UID) must not be called - use uid('store', ...)")

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            criteria = args[-1]
            if criteria == "ALL":
                return "OK", [b"5 6 7"]
            return "OK", [b"10 11 12"]
        if command == "fetch":
            spec = args[-1]
            if spec == "(FLAGS)":
                uids = args[0].split(b",")
                lines = [
                    f"1 (UID {u.decode()} FLAGS ({' '.join(self.flags_by_uid.get(u, set()))}))".encode()
                    for u in uids
                ]
                return "OK", lines
            if b"HEADER.FIELDS" in spec.encode():
                msg = MIMEText("body")
                msg["Subject"] = "Test subject"
                msg["From"] = "sender@example.com"
                msg["Date"] = "Mon, 03 Aug 2026 10:00:00 -0000"
                return "OK", [(b"1", msg.as_bytes())]
            msg = MIMEText("Full body text")
            msg["Subject"] = "Test subject"
            msg["From"] = "sender@example.com"
            msg["Message-ID"] = "<orig@example.com>"
            return "OK", [(b"1", msg.as_bytes())]
        if command == "copy":
            return "OK", [b"copied"]
        if command == "store":
            return "OK", [b""]
        raise AssertionError(f"unexpected uid() command {command!r}")

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


def test_search_messages_returns_summaries_with_flags(fake_connect):
    results = imap_client.search_messages("me@example.com", criteria="UNSEEN")
    assert len(results) == 3
    assert results[0].subject == "Test subject"
    assert results[0].sender == "sender@example.com"
    # newest-first: the fake returns uids 10 11 12, reversed
    assert [r.uid for r in results] == ["12", "11", "10"]
    assert results[0].flags == frozenset({"PangaReviewed"})  # uid 12
    assert results[1].flags == frozenset()  # uid 11


def test_search_recent_builds_since_criteria(fake_connect):
    imap_client.search_recent("me@example.com", since_days=2)
    search_call = [c for c in fake_connect.calls if c[0] == "uid" and c[1] == "search"][0]
    criteria = search_call[2][-1]
    assert criteria.startswith('SINCE "')


def test_get_message_full_body(fake_connect):
    result = imap_client.get_message("me@example.com", "10")
    assert result["subject"] == "Test subject"
    assert "Full body text" in result["body"]
    assert result["message_id_header"] == "<orig@example.com>"


def test_move_to_folder_uses_uid_copy_and_store(fake_connect):
    imap_client.move_to_folder("me@example.com", "10", "Panga/Reviewed")
    kinds = [c[1] if c[0] == "uid" else c[0] for c in fake_connect.calls]
    assert kinds == ["select", "copy", "store", "expunge", "logout"]
    store_call = [c for c in fake_connect.calls if c[0] == "uid" and c[1] == "store"][0]
    assert store_call[2] == (b"10", "+FLAGS", "\\Deleted")


def test_add_keyword_uses_uid_store(fake_connect):
    imap_client.add_keyword("me@example.com", "10", "PangaReviewed")
    store_call = [c for c in fake_connect.calls if c[0] == "uid" and c[1] == "store"][0]
    assert store_call[2] == (b"10", "+FLAGS", "PangaReviewed")


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


def test_list_and_remove_configured_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(imap_client, "IMAP_DIR", tmp_path)
    assert imap_client.list_configured_accounts() == []

    imap_client.save_credentials("a@yahoo.com", "app-pw-1", "imap.mail.yahoo.com")
    imap_client.save_credentials("b@btinternet.com", "app-pw-2", "mail.btinternet.com")
    assert sorted(imap_client.list_configured_accounts()) == ["a@yahoo.com", "b@btinternet.com"]
    assert imap_client.is_configured("a@yahoo.com")

    imap_client.remove_account("a@yahoo.com")
    assert imap_client.list_configured_accounts() == ["b@btinternet.com"]
    assert not imap_client.is_configured("a@yahoo.com")

    imap_client.remove_account("never-saved@example.com")  # no-op, must not raise
