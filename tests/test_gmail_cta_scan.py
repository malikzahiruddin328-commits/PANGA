"""Tests for scripts/gmail_cta_scan.py's CTA-to-application status
matching (added 2026-08-06, closing the gap Mirror's PRD-vs-code audit
found - the "call_to_action" bucket used to only log the email, never
match it to a specific application or call suggest_status(), despite the
PRD marking this Done). classify_thread/match_cta_application are
direct-API calls and are monkeypatched here, same "no live Anthropic
calls" scope as the rest of the regression suite. scripts/ isn't on
pytest's normal pythonpath (by convention) - this file adds it locally,
same pattern as test_fulfillment.py's predecessor did for
scripts/cta_fulfillment.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gmail_cta_scan  # noqa: E402
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


def _add_application(source, job_id, status):
    applications.upsert_application(source, job_id, status=status)


def test_rejection_match_suggests_rejected_status(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "rejection", "confident": True})
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda category, thread_summary, body, candidates: {"matched": True, "source": "usajobs", "job_id": "j1", "reason": "Explicit rejection mentioning the CIO role."})
    _add_application("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    new_items, match_count = gmail_cta_scan.scan_account(account)

    assert match_count == 1
    assert new_items == [{"subject": "Subj", "sender": "a@b.com", "category": "rejection"}]
    app = applications.get_application("usajobs", "j1")
    assert app["suggested_status"] == "rejected"
    assert app["suggested_status_reason"] == "Explicit rejection mentioning the CIO role."


def test_interview_request_match_suggests_interview_scheduled(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "interview_request", "confident": True})
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda category, thread_summary, body, candidates: {"matched": True, "source": "usajobs", "job_id": "j1", "reason": "Interview invite for the CIO role."})
    _add_application("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    gmail_cta_scan.scan_account(account)

    app = applications.get_application("usajobs", "j1")
    assert app["suggested_status"] == "interview scheduled"


def test_offer_match_suggests_offer_status(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "offer", "confident": True})
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda category, thread_summary, body, candidates: {"matched": True, "source": "usajobs", "job_id": "j1", "reason": "Formal offer letter."})
    _add_application("usajobs", "j1", "interview scheduled")

    account = FakeAccount([FakeMessage()])
    gmail_cta_scan.scan_account(account)

    app = applications.get_application("usajobs", "j1")
    assert app["suggested_status"] == "offer"


def test_assessment_request_never_attempts_a_match(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "assessment_request", "confident": True})
    match_calls = []
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda *a, **k: match_calls.append(1) or {"matched": False, "source": "", "job_id": "", "reason": ""})
    _add_application("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    new_items, match_count = gmail_cta_scan.scan_account(account)

    assert match_calls == []  # no status corresponds to assessment_request - never even tried
    assert match_count == 0
    assert new_items[0]["category"] == "assessment_request"  # still logged as a CTA item


def test_recruiter_question_never_attempts_a_match(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "recruiter_question", "confident": True})
    match_calls = []
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda *a, **k: match_calls.append(1))
    _add_application("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    gmail_cta_scan.scan_account(account)

    assert match_calls == []


def test_no_active_applications_skips_matching_without_calling_the_api(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "rejection", "confident": True})
    match_calls = []
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda *a, **k: match_calls.append(1))
    _add_application("usajobs", "j1", "rejected")  # already terminal - excluded from candidates

    account = FakeAccount([FakeMessage()])
    new_items, match_count = gmail_cta_scan.scan_account(account)

    assert match_calls == []
    assert match_count == 0
    assert new_items[0]["category"] == "rejection"  # CTA email still stored either way


def test_unmatched_email_does_not_suggest_anything(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "rejection", "confident": True})
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", lambda *a, **k: {"matched": False, "source": "", "job_id": "", "reason": "Ambiguous - two candidate jobs with the same title."})
    _add_application("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage()])
    new_items, match_count = gmail_cta_scan.scan_account(account)

    assert match_count == 0
    app = applications.get_application("usajobs", "j1")
    assert app.get("suggested_status") is None


def test_match_failure_does_not_drop_the_stored_cta_email(isolated_data, monkeypatch):
    monkeypatch.setattr(gmail_cta_scan, "classify_thread", lambda *a, **k: {"bucket": "call_to_action", "cta_category": "offer", "confident": True})

    def failing_match(*a, **k):
        raise RuntimeError("API blip")
    monkeypatch.setattr(gmail_cta_scan, "match_cta_application", failing_match)
    _add_application("usajobs", "j1", "applied")

    account = FakeAccount([FakeMessage(ref="t1")])
    new_items, match_count = gmail_cta_scan.scan_account(account)

    assert match_count == 0
    assert new_items == [{"subject": "Subj", "sender": "a@b.com", "category": "offer"}]
    stored = cta_emails.load_cta_emails()
    assert len(stored) == 1
    assert stored[0]["thread_id"] == "t1"  # the CTA email itself is unaffected by the match failure
