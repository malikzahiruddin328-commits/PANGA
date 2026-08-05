"""Tests for scripts/cta_fulfillment.py's multi-account routing logic -
the new (2026-08-04) piece that decides WHICH configured account's client
a given cta_emails.json record should be fulfilled against. Not part of
the normal src/-only test suite (scripts/ isn't on pytest's pythonpath by
convention - this file adds it locally, the same way the scripts
themselves add src/ to sys.path, rather than changing the global
pyproject.toml convention for the rest of the suite). classify_thread/
draft_cta_reply/match_application_confirmation are direct-API calls and
are never invoked for real here, same "no live Anthropic calls" scope as
the rest of the regression suite - draft_cta_reply is monkeypatched."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import cta_fulfillment  # noqa: E402
import tailoring.cta_emails as cta_emails  # noqa: E402


class FakeAccount:
    def __init__(self, provider, account):
        self.provider = provider
        self.account = account
        self.archived = []
        self.drafts_created = []
        self.draft_ids = []

    def archive(self, ref):
        self.archived.append(ref)

    def create_reply_draft(self, to, subject, body, reply_to):
        self.drafts_created.append((to, subject, body, reply_to))
        return f"draft-{len(self.drafts_created)}", f"https://example.com/draft/{len(self.drafts_created)}"

    def list_current_draft_ids(self):
        return self.draft_ids


def test_fulfill_archive_requests_routes_by_provider_and_account(isolated_data, monkeypatch):
    cta_emails.add_cta_email("t1", "S1", "a@b.com", "snip", "2026-08-04", "offer", provider="gmail", account="gmail")
    cta_emails.add_cta_email("c1", "S2", "b@b.com", "snip", "2026-08-04", "offer", provider="microsoft", account="Outlook")
    cta_emails.request_archive("t1")
    cta_emails.request_archive("c1")

    gmail_acct = FakeAccount("gmail", "gmail")
    ms_acct = FakeAccount("microsoft", "Outlook")
    accounts_by_key = {("gmail", "gmail"): gmail_acct, ("microsoft", "Outlook"): ms_acct}

    failures = cta_fulfillment.fulfill_archive_requests(accounts_by_key)
    assert failures == 0
    assert gmail_acct.archived == ["t1"]
    assert ms_acct.archived == ["c1"]
    stored = {e["thread_id"]: e for e in cta_emails.load_cta_emails()}
    assert stored["t1"]["archived"] is True
    assert stored["c1"]["archived"] is True


def test_fulfill_archive_requests_missing_account_counts_as_failure(isolated_data):
    cta_emails.add_cta_email("c1", "S", "b@b.com", "snip", "2026-08-04", "offer", provider="imap", account="gone@yahoo.com")
    cta_emails.request_archive("c1")

    failures = cta_fulfillment.fulfill_archive_requests({})  # account no longer configured
    assert failures == 1
    stored = cta_emails.load_cta_emails()[0]
    assert stored["archived"] is False  # never touched - must not silently mark it done


def test_fulfill_archive_requests_defaults_missing_fields_to_gmail(isolated_data):
    # Simulates a record written before multi-provider support existed -
    # add_cta_email always sets provider/account now, so this writes one
    # by hand the way an old stored file would look.
    emails = cta_emails.load_cta_emails()
    emails.append({
        "thread_id": "legacy1", "subject": "S", "sender": "a@b.com", "snippet": "", "date": "2026-08-01",
        "category": "offer", "dismissed": True, "archive_requested": True, "archived": False,
        "draft_requested": False, "draft_created": False, "draft_id": None, "draft_link": None, "draft_sent": False,
        # no "provider"/"account" keys at all
    })
    cta_emails._save_all(emails)

    gmail_acct = FakeAccount("gmail", "gmail")
    failures = cta_fulfillment.fulfill_archive_requests({("gmail", "gmail"): gmail_acct})
    assert failures == 0
    assert gmail_acct.archived == ["legacy1"]


def test_fulfill_draft_requests_routes_and_stores_returned_link(isolated_data, monkeypatch):
    monkeypatch.setattr(cta_fulfillment, "draft_cta_reply", lambda category, subject, snippet, available_slots=None: "Reply body")
    monkeypatch.setattr(cta_fulfillment, "_get_available_interview_slots", lambda: None)

    cta_emails.add_cta_email("c1", "Subj", "them@example.com", "snip", "2026-08-04", "offer", provider="microsoft", account="Outlook", message_id="m1")
    cta_emails.request_draft("c1")

    ms_acct = FakeAccount("microsoft", "Outlook")
    failures = cta_fulfillment.fulfill_draft_requests({("microsoft", "Outlook"): ms_acct})
    assert failures == 0
    assert ms_acct.drafts_created == [("them@example.com", "Re: Subj", "Reply body", "m1")]
    stored = cta_emails.load_cta_emails()[0]
    assert stored["draft_created"] is True
    assert stored["draft_id"] == "draft-1"
    assert stored["draft_link"] == "https://example.com/draft/1"


def test_reconcile_sent_drafts_only_checks_each_account_once(isolated_data, monkeypatch):
    monkeypatch.setattr(cta_fulfillment, "draft_cta_reply", lambda *a, **k: "Reply")
    cta_emails.add_cta_email("c1", "S1", "a@b.com", "s", "2026-08-04", "offer", provider="gmail", account="gmail")
    cta_emails.add_cta_email("c2", "S2", "b@b.com", "s", "2026-08-04", "offer", provider="gmail", account="gmail")
    cta_emails.mark_draft_created("c1", "keep-1")
    cta_emails.mark_draft_created("c2", "gone-2")

    gmail_acct = FakeAccount("gmail", "gmail")
    call_count = {"n": 0}
    real_list = gmail_acct.list_current_draft_ids

    def counting_list():
        call_count["n"] += 1
        return ["keep-1"]  # "gone-2" is no longer present - Zahir sent or deleted it
    gmail_acct.list_current_draft_ids = counting_list

    cta_fulfillment.reconcile_sent_drafts({("gmail", "gmail"): gmail_acct})
    assert call_count["n"] == 1  # one account, one lookup, even with 2 pending items

    stored = {e["thread_id"]: e for e in cta_emails.load_cta_emails()}
    assert stored["c1"]["dismissed"] is False
    assert stored["c2"]["dismissed"] is True
    assert stored["c2"]["archive_requested"] is True
