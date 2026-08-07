"""Tests for scripts/job_alert_scan.py - same "monkeypatch the direct-API
reasoning call, add scripts/ to sys.path locally" pattern as
test_gmail_cta_scan.py. extract_listings is monkeypatched here; real
dedup/save behavior goes through the real search.job_store functions
against isolated_data's tmp_path store."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import job_alert_scan  # noqa: E402
import search.job_store as job_store  # noqa: E402


class FakeMessage:
    def __init__(self, ref="m1", subject="5 new jobs for you", sender="jobalerts-noreply@linkedin.com", date="2026-08-07", snippet="snip"):
        self.ref = ref
        self.subject = subject
        self.sender = sender
        self.date = date
        self.snippet = snippet


class FakeAccount:
    provider = "gmail"
    account = "gmail"

    def __init__(self, messages):
        self._messages = messages
        self.marked = []

    def list_job_alert_candidates(self, senders):
        return self._messages

    def get_body(self, ref):
        return "full digest body"

    def mark_job_alert_reviewed(self, ref):
        self.marked.append(ref)


_SENDERS = [
    {"sender": "jobalerts-noreply@linkedin.com", "source": "linkedin"},
    {"sender": "lensa.com", "source": "lensa"},
]


def test_extracted_listing_is_saved_with_mapped_source(isolated_data, monkeypatch):
    monkeypatch.setattr(job_alert_scan, "extract_listings", lambda subject, body: [
        {"title": "CIO", "organization": "Acme Corp", "location": "Remote", "posting_url": "https://linkedin.com/jobs/view/12345", "description": ""},
    ])
    account = FakeAccount([FakeMessage()])

    new_jobs = job_alert_scan.scan_account(account, _SENDERS)

    assert len(new_jobs) == 1
    assert new_jobs[0]["source"] == "linkedin"
    assert new_jobs[0]["job_id"] == "12345"
    stored = job_store.load_jobs()
    assert len(stored) == 1
    assert stored[0]["organization"] == "Acme Corp"


def test_message_marked_job_alert_reviewed_even_with_no_listings(isolated_data, monkeypatch):
    monkeypatch.setattr(job_alert_scan, "extract_listings", lambda subject, body: [])
    account = FakeAccount([FakeMessage(ref="m1")])

    job_alert_scan.scan_account(account, _SENDERS)

    assert account.marked == ["m1"]
    assert job_store.load_jobs() == []


def test_multiple_listings_in_one_digest_all_saved(isolated_data, monkeypatch):
    monkeypatch.setattr(job_alert_scan, "extract_listings", lambda subject, body: [
        {"title": "CIO", "organization": "Acme Corp", "location": "Remote", "posting_url": "https://linkedin.com/jobs/view/111", "description": ""},
        {"title": "VP IT", "organization": "Beta Inc", "location": "NYC", "posting_url": "https://linkedin.com/jobs/view/222", "description": ""},
    ])
    account = FakeAccount([FakeMessage()])

    new_jobs = job_alert_scan.scan_account(account, _SENDERS)

    assert len(new_jobs) == 2
    assert {j["job_id"] for j in new_jobs} == {"111", "222"}


def test_listing_with_no_posting_url_is_skipped(isolated_data, monkeypatch):
    monkeypatch.setattr(job_alert_scan, "extract_listings", lambda subject, body: [
        {"title": "CIO", "organization": "Acme Corp", "location": "Remote", "posting_url": "", "description": ""},
    ])
    account = FakeAccount([FakeMessage()])

    new_jobs = job_alert_scan.scan_account(account, _SENDERS)

    assert new_jobs == []
    assert job_store.load_jobs() == []


def test_thin_listing_missing_organization_still_saved(isolated_data, monkeypatch):
    # The paste-JD-manually fallback (ui/app.py's
    # render_paste_jd_prompt_before_drafting) already triggers off an empty
    # description/organization - this scan must not fabricate one to "fill
    # the gap" itself, and must not drop the listing just because it's thin.
    monkeypatch.setattr(job_alert_scan, "extract_listings", lambda subject, body: [
        {"title": "CIO", "organization": "", "location": "", "posting_url": "https://linkedin.com/jobs/view/999", "description": ""},
    ])
    account = FakeAccount([FakeMessage()])

    new_jobs = job_alert_scan.scan_account(account, _SENDERS)

    assert len(new_jobs) == 1
    assert new_jobs[0]["organization"] == ""


def test_extraction_failure_does_not_crash_and_still_marks_reviewed(isolated_data, monkeypatch):
    def failing_extract(subject, body):
        raise RuntimeError("API blip")
    monkeypatch.setattr(job_alert_scan, "extract_listings", failing_extract)
    account = FakeAccount([FakeMessage(ref="m1")])

    new_jobs = job_alert_scan.scan_account(account, _SENDERS)

    assert new_jobs == []
    # A failed extraction shouldn't mark the message reviewed - unlike a
    # successful-but-empty extraction, this is a transient failure worth
    # retrying on the next run, not a genuine "nothing here."
    assert account.marked == []


def test_dedup_across_two_runs_does_not_duplicate(isolated_data, monkeypatch):
    monkeypatch.setattr(job_alert_scan, "extract_listings", lambda subject, body: [
        {"title": "CIO", "organization": "Acme Corp", "location": "Remote", "posting_url": "https://linkedin.com/jobs/view/12345", "description": ""},
    ])
    account = FakeAccount([FakeMessage()])

    first_run = job_alert_scan.scan_account(account, _SENDERS)
    second_run = job_alert_scan.scan_account(account, _SENDERS)

    assert len(first_run) == 1
    assert len(second_run) == 1  # same listing re-extracted (e.g. a re-run) still dedupes via job_store
    assert len(job_store.load_jobs()) == 1


def test_senders_source_for_maps_sender_header_to_configured_source():
    assert job_alert_scan.senders_source_for("LinkedIn Jobs <jobalerts-noreply@linkedin.com>", _SENDERS) == "linkedin"
    assert job_alert_scan.senders_source_for("Lensa <no-reply@lensa.com>", _SENDERS) == "lensa"


def test_senders_source_for_falls_back_when_unmatched():
    assert job_alert_scan.senders_source_for("someone@unrelated.com", _SENDERS) == "job alert"


def test_run_skips_when_no_senders_configured(isolated_data, monkeypatch):
    monkeypatch.setattr(job_alert_scan, "load_job_alert_senders", lambda: [])
    called = []
    monkeypatch.setattr(job_alert_scan, "configured_accounts", lambda: called.append(1))
    job_alert_scan.run()
    assert called == []  # never even asks which accounts are configured
