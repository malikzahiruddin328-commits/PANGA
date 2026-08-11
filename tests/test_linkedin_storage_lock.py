"""Tests for the file-locking fix on linkedin/storage.py (2026-08-10, PRD
S13 #25, Zahir's whole-codebase adversarial self-audit request). Real
concurrent-writer exposure: Streamlit serves each open browser tab of the
same running process on its own thread - save_snapshot() (Settings'
uploader), save_analysis() (LinkedIn tab's "Analyze profile" button), and
mark_suggestion_status() (its Mark updated/Dismiss buttons) are all
reachable from a normal Streamlit session and can race on this file
exactly like every other store in this app already does.
"""

import threading

import linkedin.storage as storage


class RecordingLock:
    """Stand-in for security.file_lock.locked() that records whether it
    was held while the save actually happened, without touching a real
    lock file - same pattern as tests/test_gmail_client.py (commit
    81259f8), test_outreach.py and test_target_accounts.py (2026-08-09/10)."""

    def __init__(self, calls, name):
        self.calls = calls
        self.name = name

    def __enter__(self):
        self.calls.append(("enter", self.name))
        return self

    def __exit__(self, *exc):
        self.calls.append(("exit", self.name))
        return False


def _suggestion(section="headline", suggested="New headline"):
    return {"section": section, "current_text": "", "suggested_text": suggested, "rationale": "why"}


def test_save_snapshot_runs_inside_the_lock(isolated_data, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "locked", lambda name: RecordingLock(calls, name))
    storage.save_snapshot("raw text", ["Profile.pdf"], "2026-08-10T00:00:00+00:00")
    assert calls[0] == ("enter", "linkedin_profile")
    assert calls[-1] == ("exit", "linkedin_profile")
    assert storage.load_linkedin_profile()["raw_text"] == "raw text"


def test_save_analysis_runs_inside_the_lock(isolated_data, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "locked", lambda name: RecordingLock(calls, name))
    storage.save_analysis([_suggestion()], 72, "rationale text", "2026-08-10T00:00:00+00:00")
    assert calls[0] == ("enter", "linkedin_profile")
    assert calls[-1] == ("exit", "linkedin_profile")
    assert storage.load_linkedin_profile()["profile_strength_score"] == 72


def test_mark_suggestion_status_runs_inside_the_lock(isolated_data, monkeypatch):
    storage.save_analysis([_suggestion()], 72, "rationale text", "2026-08-10T00:00:00+00:00")
    suggestion_id = storage.load_linkedin_profile()["suggestions"][0]["id"]

    calls = []
    monkeypatch.setattr(storage, "locked", lambda name: RecordingLock(calls, name))
    storage.mark_suggestion_status(suggestion_id, "applied")
    assert calls[0] == ("enter", "linkedin_profile")
    assert calls[-1] == ("exit", "linkedin_profile")


def test_concurrent_mark_suggestion_status_loses_no_updates(isolated_data):
    """Real proof against the actual msvcrt lock (not a mock): 8 threads
    each marking a DIFFERENT suggestion "applied" concurrently on the same
    underlying file - if the lock isn't actually serializing these read-
    modify-write calls, some updates silently overwrite each other's
    write, same failure mode as the outreach.json/target_accounts.json
    bugs this mirrors."""
    threads_n = 8
    suggestions = [_suggestion(section="experience", suggested=f"Bullet {i}") for i in range(threads_n)]
    storage.save_analysis(suggestions, 50, "rationale", "2026-08-10T00:00:00+00:00")
    ids = [s["id"] for s in storage.load_linkedin_profile()["suggestions"]]

    def worker(suggestion_id):
        storage.mark_suggestion_status(suggestion_id, "applied")

    threads = [threading.Thread(target=worker, args=(sid,)) for sid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a thread hung - possible deadlock in locked()"

    final = storage.load_linkedin_profile()["suggestions"]
    assert all(s["status"] == "applied" for s in final)
    assert len(final) == threads_n
