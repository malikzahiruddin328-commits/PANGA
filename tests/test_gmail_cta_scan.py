"""Tests for scripts/gmail_cta_scan.py's CTA-to-application status
matching (added 2026-08-06, closing the gap Mirror's PRD-vs-code audit
found - the "call_to_action" bucket used to only log the email, never
match it to a specific application or call suggest_status(), despite the
PRD marking this Done). classify_thread/match_cta_application are
direct-API calls and are monkeypatched here, same "no live Anthropic
calls" scope as the rest of the regression suite. scripts/ isn't on
pytest's normal pythonpath (by convention) - this file adds it locally,
same pattern as test_fulfillment.py's predecessor did for
scripts/cta_fulfillment.py.

Candidate-job join (2026-08-07): applications.json alone has no
title/organization (see applications.py's docstring) - every test here
that expects a candidate to actually be matchable also creates the
corresponding job record via _add_application_with_job(), same as
gmail_cta_scan.py's real jobs_by_key join requires. See
test_match_receives_title_and_organization_from_joined_job_record and
test_application_with_no_job_record_is_excluded_from_candidates for
tests of the join itself."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gmail_cta_scan  # noqa: E402
import search.job_store as job_store  # noqa: E402
import tailoring.applications as applications  # noqa: E402
import tailoring.cta_emails as cta_emails  # noqa: E402


class FakeMessage:
    def __init__(self, ref="m1", subject="Subj", sender="a@b.com", date="2026-08-06", snippet="snip", message_id="mid1"):
        self.ref = ref
        self.subject = subject
        self.sender = sender
        self.date = date
        self.snippet = snippet
        self.message_id = message_id


class FakeAccount:
    provider = "gmail"
    account = "gmail"

    def __init__(self, messages):
        self._messages = messages
        self.marked_cta = []
        self.marked_reviewed = []

    def list_recent_unreviewed(self):
        return self._messages

    def get_body(self, ref):
        return "full body text"

    def mark_reviewed(self, ref):
        self.marked_reviewed.append(ref)

    def mark_cta(self, ref):
        self.marked_cta.append(ref)

    def web_link(self, ref):
        return f"https://example.com/{ref}"


def _jobs_by_key():
    return {(j["source"], j["job_id"]): j for j in job_store.load_jobs()}


def _add_application(source, job_id, status):
    """Application only - no job record. Used by tests that expect the
    join to correctly EXCLUDE this candidate (see
    test_application_with_no_job_record_is_excluded_from_candidates)."""
    applications.upsert_application(source, job_id, status=status)


def _add_application_with_job(source, job_id, status, title="CIO", organization="Acme Corp"):
    """The normal case: an application whose job record actually exists,
    so it's a real, matchable candidate once joined."""
    job_store.save_jobs([{"source": source, "job_id": job_id, "title": title, "organization": organization, "location": "", "description": "", "posting_url": "https://example.com"}])
    applications.upsert_application(source, job_id, status=status)


def test_rejection_match_suggests_rejected_status(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "rejection", "confident": True})
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda category, thread_summary, body, candidates: {"matched": True, "source": "usajobs", "job_id": "j1", "reason": "Explicit rejection mentioning the CIO role."})
    _add_application_with_job("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    new_items, match_count = gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert match_count == 1
    assert new_items == [{"subject": "Subj", "sender": "a@b.com", "category": "rejection"}]
    app = applications.get_application("usajobs", "j1")
    assert app["suggested_status"] == "rejected"
    assert app["suggested_status_reason"] == "Explicit rejection mentioning the CIO role."


def test_interview_request_match_suggests_interview_scheduled(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "interview_request", "confident": True})
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda category, thread_summary, body, candidates: {"matched": True, "source": "usajobs", "job_id": "j1", "reason": "Interview invite for the CIO role."})
    _add_application_with_job("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    gmail_cta_scan.scan_account(account, _jobs_by_key())

    app = applications.get_application("usajobs", "j1")
    assert app["suggested_status"] == "interview scheduled"


def test_offer_match_suggests_offer_status(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "offer", "confident": True})
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda category, thread_summary, body, candidates: {"matched": True, "source": "usajobs", "job_id": "j1", "reason": "Formal offer letter."})
    _add_application_with_job("usajobs", "j1", "interview scheduled")

    account = FakeAccount([FakeMessage()])
    gmail_cta_scan.scan_account(account, _jobs_by_key())

    app = applications.get_application("usajobs", "j1")
    assert app["suggested_status"] == "offer"


def test_assessment_request_never_attempts_a_match(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "assessment_request", "confident": True})
    match_calls = []
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda *a, **k: match_calls.append(1) or {"matched": False, "source": "", "job_id": "", "reason": ""})
    _add_application_with_job("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    new_items, match_count = gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert match_calls == []  # no status corresponds to assessment_request - never even tried
    assert match_count == 0
    assert new_items[0]["category"] == "assessment_request"  # still logged as a CTA item


def test_recruiter_question_never_attempts_a_match(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "recruiter_question", "confident": True})
    match_calls = []
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda *a, **k: match_calls.append(1))
    _add_application_with_job("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert match_calls == []


def test_no_active_applications_skips_matching_without_calling_the_api(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "rejection", "confident": True})
    match_calls = []
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda *a, **k: match_calls.append(1))
    _add_application_with_job("usajobs", "j1", "rejected")  # already terminal - excluded from candidates

    account = FakeAccount([FakeMessage()])
    new_items, match_count = gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert match_calls == []
    assert match_count == 0
    assert new_items[0]["category"] == "rejection"  # CTA email still stored either way


def test_unmatched_email_does_not_suggest_anything(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "rejection", "confident": True})
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda *a, **k: {"matched": False, "source": "", "job_id": "", "reason": "Ambiguous - two candidate jobs with the same title."})
    _add_application_with_job("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    new_items, match_count = gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert match_count == 0
    app = applications.get_application("usajobs", "j1")
    assert app.get("suggested_status") is None


def test_match_failure_does_not_drop_the_stored_cta_email(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "offer", "confident": True})

    def failing_match(*a, **k):
        raise RuntimeError("API blip")
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", failing_match)
    _add_application_with_job("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage(ref="t1")])
    new_items, match_count = gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert match_count == 0
    assert new_items == [{"subject": "Subj", "sender": "a@b.com", "category": "offer"}]
    stored = cta_emails.load_cta_emails()
    assert len(stored) == 1
    assert stored[0]["thread_id"] == "t1"  # the CTA email itself is unaffected by the match failure


# ---- Candidate-job join (2026-08-07) - the real gap General's relayed
# scheduled-task run report flagged: applications alone have no
# title/organization, so the match calls had nothing real to compare an
# email's content against.

def test_match_receives_title_and_organization_from_joined_job_record(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "rejection", "confident": True})
    captured = {}

    def capturing_match(category, thread_summary, body, candidates):
        captured["candidates"] = candidates
        return {"matched": False, "source": "", "job_id": "", "reason": ""}
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", capturing_match)
    _add_application_with_job("usajobs", "j1", "applied", title="Chief Information Officer", organization="Acme Corp")

    account = FakeAccount([FakeMessage()])
    gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert captured["candidates"] == [
        {"source": "usajobs", "job_id": "j1", "status": "applied", "title": "Chief Information Officer", "organization": "Acme Corp", "location": ""},
    ]


def test_application_with_no_job_record_is_excluded_from_candidates(isolated_data, monkeypatch):
    # An application with no corresponding job_store entry (shouldn't
    # normally happen, but not guaranteed) must not be handed to the match
    # call with blank title/organization - that would just reintroduce
    # the "nothing to compare against" bug for that one candidate.
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "rejection", "confident": True})
    match_calls = []
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda *a, **k: match_calls.append(1))
    _add_application("usajobs", "orphan1", "applied")  # no matching job record

    account = FakeAccount([FakeMessage()])
    gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert match_calls == []  # no real candidates once the orphan is excluded - never even called


def test_application_confirmation_match_also_uses_joined_candidates(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "application_confirmation", "confident": True})
    captured = {}

    def capturing_match(thread_summary, body, candidates):
        captured["candidates"] = candidates
        return {"matched": True, "source": "usajobs", "job_id": "j1", "reason": "Confirmed receipt."}
    monkeypatch.setattr(gmail_cta_scan, "match_application_confirmation", capturing_match)
    _add_application_with_job("usajobs", "j1", "under review", title="VP IT", organization="Beta Inc")

    account = FakeAccount([FakeMessage()])
    new_items, match_count = gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert match_count == 1
    assert captured["candidates"] == [
        {"source": "usajobs", "job_id": "j1", "status": "under review", "title": "VP IT", "organization": "Beta Inc", "location": ""},
    ]
    app = applications.get_application("usajobs", "j1")
    assert app["suggested_status"] == "applied"


# ---- Mark-reviewed-on-every-terminal-path (2026-08-09) - real bug found
# by Backlog against the live code: not_related never marked reviewed at
# all, and a classification failure skipped marking entirely (unbounded
# retry). See scripts/gmail_cta_scan.py's module docstring.

def test_not_related_gets_marked_reviewed(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "not_related", "cta_category": "", "confident": True})

    account = FakeAccount([FakeMessage(ref="m1")])
    gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert account.marked_reviewed == ["m1"]  # the actual bug: this used to be []


def test_classification_failure_does_not_mark_reviewed_before_max_attempts(isolated_data, monkeypatch):
    def failing_classify(*a, **k):
        raise RuntimeError("API blip")
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", failing_classify)

    account = FakeAccount([FakeMessage(ref="m1")])
    gmail_cta_scan.scan_account(account, _jobs_by_key())

    assert account.marked_reviewed == []  # first failure - still gets a retry next run, not marked reviewed


def test_classification_failure_gives_up_and_marks_reviewed_after_max_attempts(isolated_data, monkeypatch):
    def failing_classify(*a, **k):
        raise RuntimeError("permanently malformed body")
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", failing_classify)

    account = FakeAccount([FakeMessage(ref="m1")])
    for _ in range(gmail_cta_scan.MAX_ATTEMPTS - 1):
        gmail_cta_scan.scan_account(account, _jobs_by_key())
        assert account.marked_reviewed == []  # still retrying

    gmail_cta_scan.scan_account(account, _jobs_by_key())
    assert account.marked_reviewed == ["m1"]  # gave up on the MAX_ATTEMPTS-th failure


def test_success_after_a_prior_failure_clears_the_retry_count(isolated_data, monkeypatch):
    calls = {"n": 0}

    def flaky_classify(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient blip")
        return {"bucket": "passive", "cta_category": "", "confident": True}
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", flaky_classify)

    account = FakeAccount([FakeMessage(ref="m1")])
    gmail_cta_scan.scan_account(account, _jobs_by_key())  # fails once
    assert account.marked_reviewed == []

    account2 = FakeAccount([FakeMessage(ref="m1")])
    gmail_cta_scan.scan_account(account2, _jobs_by_key())  # succeeds - clears the count
    assert account2.marked_reviewed == ["m1"]

    # A fresh failure afterward should start counting from 1 again, not
    # continue from the earlier failure - confirms clear_failure ran.
    import scan_retry_tracker
    assert scan_retry_tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "m1") == 1
