"""Tests for src/fulfillment.py - the shared archive/draft/reconcile logic
used by both the scheduled task and the dashboard's synchronous "Send and
receive" button. classify_thread/draft_cta_reply/draft_outreach_email are
direct-API calls and are monkeypatched here, same "no live Anthropic
calls" scope as the rest of the regression suite."""

import fulfillment
import prospector.outreach as outreach
import search.job_store as job_store
import tailoring.cta_emails as cta_emails


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


# ---- last-synced / pending count ----

def test_last_synced_starts_none_and_updates(isolated_data):
    assert fulfillment.get_last_synced_at() is None
    fulfillment._record_sync_completed()
    first = fulfillment.get_last_synced_at()
    assert first is not None
    fulfillment._record_sync_completed()
    assert fulfillment.get_last_synced_at() >= first


def test_get_pending_count_sums_all_three_sources(isolated_data):
    assert fulfillment.get_pending_count() == 0

    cta_emails.add_cta_email("t1", "S", "a@b.com", "s", "2026-08-05", "offer")
    cta_emails.request_archive("t1")
    cta_emails.add_cta_email("t2", "S2", "b@b.com", "s", "2026-08-05", "offer")
    cta_emails.request_draft("t2")
    outreach.add_outreach("Contact", "email", target_account_name="Acme", contact_email="c@acme.com")
    records = outreach.load_outreach()
    outreach.request_draft(records[0]["outreach_id"])

    assert fulfillment.get_pending_count() == 3


# ---- outreach email validity ----

def test_looks_like_valid_email():
    assert fulfillment._looks_like_valid_email("real@example.com")
    assert not fulfillment._looks_like_valid_email(None)
    assert not fulfillment._looks_like_valid_email("")
    assert not fulfillment._looks_like_valid_email("not-an-email")
    assert not fulfillment._looks_like_valid_email("noreply@example.com")
    assert not fulfillment._looks_like_valid_email("careers-no-reply@example.com")


# ---- context resolution ----

def test_resolve_context_prefers_target_account_with_notes(isolated_data):
    record = {"target_account_name": "Acme Corp", "notes": "Hiring for CIO"}
    context = fulfillment._resolve_outreach_context(record)
    assert "Acme Corp" in context and "Hiring for CIO" in context


def test_resolve_context_falls_back_to_linked_job(isolated_data):
    job_store.save_jobs([{"source": "usajobs", "job_id": "j1", "title": "CIO", "organization": "Dept of Example", "url": "https://x"}])
    record = {"job_source": "usajobs", "job_id": "j1"}
    context = fulfillment._resolve_outreach_context(record)
    assert "CIO" in context and "Dept of Example" in context


def test_resolve_context_generic_fallback_when_nothing_resolves(isolated_data):
    record = {"job_source": "usajobs", "job_id": "does-not-exist"}
    context = fulfillment._resolve_outreach_context(record)
    assert isinstance(context, str) and context


# ---- fulfill_outreach_draft_requests ----

def test_fulfill_outreach_skips_invalid_email(isolated_data, monkeypatch):
    monkeypatch.setattr(fulfillment, "draft_outreach_email", lambda *a, **k: "Body")
    outreach.add_outreach("Contact", "email", target_account_name="Acme", contact_email="noreply@acme.com")
    outreach.request_draft(outreach.load_outreach()[0]["outreach_id"])

    gmail_acct = FakeAccount("gmail", "gmail")
    failures = fulfillment.fulfill_outreach_draft_requests({("gmail", "gmail"): gmail_acct})
    assert failures == 0
    assert gmail_acct.drafts_created == []  # skipped, not drafted
    record = outreach.load_outreach()[0]
    assert record["draft_requested"] is True  # never cleared - a human needs to fix the address, not silently drop the request
    assert record["draft_id"] is None
    assert outreach.get_pending_draft_requests() == [record]  # still shows as pending, not silently dismissed


def test_fulfill_outreach_drafts_with_valid_email(isolated_data, monkeypatch):
    monkeypatch.setattr(fulfillment, "draft_outreach_email", lambda contact_name, contact_title, context: f"Intro for {contact_name}")
    outreach.add_outreach("Jane Doe", "email", target_account_name="Acme", contact_email="jane@acme.com", contact_title="VP Eng")
    outreach.request_draft(outreach.load_outreach()[0]["outreach_id"])

    gmail_acct = FakeAccount("gmail", "gmail")
    failures = fulfillment.fulfill_outreach_draft_requests({("gmail", "gmail"): gmail_acct})
    assert failures == 0
    assert gmail_acct.drafts_created == [("jane@acme.com", "Introduction - Zahir Uddin", "Intro for Jane Doe", None)]
    record = outreach.load_outreach()[0]
    assert record["draft_id"] == "draft-1"
    assert record["provider"] == "gmail"
    assert record["status"] == "drafted"


def test_fulfill_outreach_prefers_gmail_when_multiple_accounts_configured(isolated_data, monkeypatch):
    monkeypatch.setattr(fulfillment, "draft_outreach_email", lambda *a, **k: "Body")
    outreach.add_outreach("Contact", "email", target_account_name="Acme", contact_email="c@acme.com")
    outreach.request_draft(outreach.load_outreach()[0]["outreach_id"])

    gmail_acct = FakeAccount("gmail", "gmail")
    imap_acct = FakeAccount("imap", "me@yahoo.com")
    fulfillment.fulfill_outreach_draft_requests({("imap", "me@yahoo.com"): imap_acct, ("gmail", "gmail"): gmail_acct})
    assert len(gmail_acct.drafts_created) == 1
    assert len(imap_acct.drafts_created) == 0


def test_fulfill_outreach_falls_back_to_only_available_account(isolated_data, monkeypatch):
    monkeypatch.setattr(fulfillment, "draft_outreach_email", lambda *a, **k: "Body")
    outreach.add_outreach("Contact", "email", target_account_name="Acme", contact_email="c@acme.com")
    outreach.request_draft(outreach.load_outreach()[0]["outreach_id"])

    imap_acct = FakeAccount("imap", "me@yahoo.com")
    fulfillment.fulfill_outreach_draft_requests({("imap", "me@yahoo.com"): imap_acct})
    assert len(imap_acct.drafts_created) == 1
    record = outreach.load_outreach()[0]
    assert record["provider"] == "imap"
    assert record["account"] == "me@yahoo.com"


def test_fulfill_outreach_no_accounts_configured_is_not_a_failure(isolated_data):
    outreach.add_outreach("Contact", "email", target_account_name="Acme", contact_email="c@acme.com")
    outreach.request_draft(outreach.load_outreach()[0]["outreach_id"])
    assert fulfillment.fulfill_outreach_draft_requests({}) == 0


# ---- reconcile_sent_outreach_drafts ----

def test_reconcile_sent_outreach_drafts(isolated_data, monkeypatch):
    monkeypatch.setattr(fulfillment, "draft_outreach_email", lambda *a, **k: "Body")
    outreach.add_outreach("A", "email", target_account_name="Acme1", contact_email="a@acme.com")
    outreach.add_outreach("B", "email", target_account_name="Acme2", contact_email="b@acme.com")
    records = outreach.load_outreach()
    outreach.mark_draft_created(records[0]["outreach_id"], "keep-1", provider="gmail", account="gmail")
    outreach.mark_draft_created(records[1]["outreach_id"], "gone-2", provider="gmail", account="gmail")

    gmail_acct = FakeAccount("gmail", "gmail")
    gmail_acct.draft_ids = ["keep-1"]  # "gone-2" no longer present - sent or deleted
    fulfillment.reconcile_sent_outreach_drafts({("gmail", "gmail"): gmail_acct})

    updated = {r["outreach_id"]: r for r in outreach.load_outreach()}
    assert updated[records[0]["outreach_id"]]["status"] == "drafted"
    assert updated[records[1]["outreach_id"]]["status"] == "sent"


# ---- run_full_fulfillment (end to end) ----

def test_run_full_fulfillment_summary_and_sync_timestamp(isolated_data, monkeypatch):
    monkeypatch.setattr(fulfillment, "configured_accounts", lambda: [FakeAccount("gmail", "gmail")])
    monkeypatch.setattr(fulfillment, "draft_cta_reply", lambda *a, **k: "Reply body")
    monkeypatch.setattr(fulfillment, "draft_outreach_email", lambda *a, **k: "Intro body")
    monkeypatch.setattr(fulfillment, "_get_available_interview_slots", lambda: None)

    cta_emails.add_cta_email("t1", "S", "a@b.com", "s", "2026-08-05", "offer")
    cta_emails.request_archive("t1")
    cta_emails.add_cta_email("t2", "S2", "b@b.com", "s", "2026-08-05", "offer")
    cta_emails.request_draft("t2")
    outreach.add_outreach("C", "email", target_account_name="Acme", contact_email="c@acme.com")
    outreach.request_draft(outreach.load_outreach()[0]["outreach_id"])

    assert fulfillment.get_last_synced_at() is None
    summary = fulfillment.run_full_fulfillment()
    assert summary == {"archived": 1, "cta_drafts": 1, "outreach_drafts": 1, "failures": 0}
    assert fulfillment.get_last_synced_at() is not None
    assert fulfillment.get_pending_count() == 0


def test_run_full_fulfillment_records_sync_even_with_failures(isolated_data, monkeypatch):
    monkeypatch.setattr(fulfillment, "configured_accounts", lambda: [])  # nothing configured
    cta_emails.add_cta_email("t1", "S", "a@b.com", "s", "2026-08-05", "offer")
    cta_emails.request_archive("t1")

    summary = fulfillment.run_full_fulfillment()
    assert summary["failures"] >= 1
    assert fulfillment.get_last_synced_at() is not None  # still recorded - "last ran," not "last ran clean"
