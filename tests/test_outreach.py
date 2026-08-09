"""Tests for prospector/outreach.py, including the file-locking fix
(2026-08-09, Mirror's audit): fulfillment.py's own docstring already
claimed outreach.json was covered by security.file_lock.locked() like
applications.json/cta_emails.json, but it wasn't - the scheduled
fulfillment task and the dashboard's manual "Send and receive" button are
two separate processes that can genuinely write to this store at close to
the same moment. No storage-layer tests existed for this module before
this file - covers the basic CRUD surface plus the locking fix itself.
"""

import threading

import prospector.outreach as outreach


class RecordingLock:
    """Stand-in for security.file_lock.locked() that records whether it
    was held while the save actually happened, without touching a real
    lock file - same pattern as tests/test_gmail_client.py's fix for the
    OAuth-token race (commit 81259f8)."""

    def __init__(self, calls, name):
        self.calls = calls
        self.name = name

    def __enter__(self):
        self.calls.append(("enter", self.name))
        return self

    def __exit__(self, *exc):
        self.calls.append(("exit", self.name))
        return False


# ---- basic CRUD ----

def test_add_outreach_requires_job_or_target_account(isolated_data):
    try:
        outreach.add_outreach("Contact", "email")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_add_outreach_creates_planned_record(isolated_data):
    outreach_id = outreach.add_outreach(
        "Jane Doe", "email", target_account_name="Acme", contact_title="VP Eng", contact_email="jane@acme.com",
    )
    records = outreach.load_outreach()
    assert len(records) == 1
    r = records[0]
    assert r["outreach_id"] == outreach_id
    assert r["status"] == "planned"
    assert r["target_account_name"] == "Acme"
    assert r["created_at"] == r["status_updated_at"]


def test_update_status_bumps_timestamp_only_on_real_change(isolated_data):
    outreach_id = outreach.add_outreach("Contact", "email", target_account_name="Acme")
    before = outreach.load_outreach()[0]["status_updated_at"]

    outreach.update_status(outreach_id, "planned")  # no-op, same status
    assert outreach.load_outreach()[0]["status_updated_at"] == before

    outreach.update_status(outreach_id, "sent", notes="left a voicemail")
    r = outreach.load_outreach()[0]
    assert r["status"] == "sent"
    assert r["notes"] == "left a voicemail"
    assert r["status_updated_at"] != before


def test_set_strategy_tag(isolated_data):
    outreach_id = outreach.add_outreach("Contact", "email", target_account_name="Acme")
    outreach.set_strategy_tag(outreach_id, "warm-intro")
    assert outreach.load_outreach()[0]["strategy_tag"] == "warm-intro"


def test_request_draft_then_mark_draft_created_transitions_planned_to_drafted(isolated_data):
    outreach_id = outreach.add_outreach("Contact", "email", target_account_name="Acme", contact_email="c@acme.com")
    outreach.request_draft(outreach_id)
    assert outreach.get_pending_draft_requests() == outreach.load_outreach()

    outreach.mark_draft_created(outreach_id, "draft123")
    r = outreach.load_outreach()[0]
    assert r["status"] == "drafted"
    assert r["draft_requested"] is False
    assert r["draft_id"] == "draft123"
    assert r["draft_link"] == "https://mail.google.com/mail/u/0/#drafts?compose=draft123"
    assert outreach.get_pending_draft_requests() == []
    assert outreach.get_awaiting_draft_send() == [r]


def test_mark_draft_created_non_gmail_no_link_unless_given(isolated_data):
    outreach_id = outreach.add_outreach("Contact", "email", target_account_name="Acme")
    outreach.mark_draft_created(outreach_id, "draft123", provider="imap", account="me@yahoo.com")
    assert outreach.load_outreach()[0]["draft_link"] is None


def test_mark_draft_sent(isolated_data):
    outreach_id = outreach.add_outreach("Contact", "email", target_account_name="Acme")
    outreach.mark_draft_created(outreach_id, "draft123")
    outreach.mark_draft_sent(outreach_id)
    r = outreach.load_outreach()[0]
    assert r["status"] == "sent"
    assert outreach.get_awaiting_draft_send() == []


def test_get_outreach_for_target_account_and_job(isolated_data):
    outreach.add_outreach("A", "email", target_account_name="Acme")
    outreach.add_outreach("B", "email", job_source="linkedin", job_id="123")
    assert len(outreach.get_outreach_for_target_account("acme")) == 1  # case-insensitive
    assert len(outreach.get_outreach_for_job("linkedin", "123")) == 1
    assert outreach.get_outreach_for_job("linkedin", "999") == []


# ---- locking fix (2026-08-09) ----

def test_add_outreach_save_runs_inside_the_lock(isolated_data, monkeypatch):
    calls = []
    monkeypatch.setattr(outreach, "locked", lambda name: RecordingLock(calls, name))
    outreach.add_outreach("Contact", "email", target_account_name="Acme")
    assert calls[0] == ("enter", "outreach")
    assert calls[-1] == ("exit", "outreach")
    # the save must have happened before the lock was released - proven by
    # the record actually existing on disk (load_outreach also isn't
    # locked, so this would still pass even if the write happened after
    # exit; the real proof is the concurrency test below)
    assert len(outreach.load_outreach()) == 1


def test_update_status_runs_inside_the_lock(isolated_data, monkeypatch):
    outreach_id = outreach.add_outreach("Contact", "email", target_account_name="Acme")
    calls = []
    monkeypatch.setattr(outreach, "locked", lambda name: RecordingLock(calls, name))
    outreach.update_status(outreach_id, "sent")
    assert calls[0] == ("enter", "outreach")
    assert calls[-1] == ("exit", "outreach")


def test_mark_draft_created_runs_inside_the_lock(isolated_data, monkeypatch):
    outreach_id = outreach.add_outreach("Contact", "email", target_account_name="Acme")
    calls = []
    monkeypatch.setattr(outreach, "locked", lambda name: RecordingLock(calls, name))
    outreach.mark_draft_created(outreach_id, "draft123")
    assert calls[0] == ("enter", "outreach")
    assert calls[-1] == ("exit", "outreach")


def test_concurrent_add_outreach_loses_no_records(isolated_data):
    """Real proof, not just a mock recording an enter/exit call: without
    the lock, two threads both doing load-append-save on the same file
    race - whichever finishes last silently drops the other's append,
    same failure mode as the OAuth token bug this mirrors (commit
    81259f8). 8 threads x 5 records each, using the REAL msvcrt lock (not
    monkeypatched) against a real file under tmp_path - if any record goes
    missing, the lock isn't actually serializing the writes."""
    threads_n, per_thread = 8, 5

    def worker(i):
        for j in range(per_thread):
            outreach.add_outreach(f"Contact {i}-{j}", "email", target_account_name=f"Acme {i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a thread hung - possible deadlock in locked()"

    records = outreach.load_outreach()
    assert len(records) == threads_n * per_thread
    assert len({r["outreach_id"] for r in records}) == threads_n * per_thread
