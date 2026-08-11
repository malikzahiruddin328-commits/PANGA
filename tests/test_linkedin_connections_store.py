"""Tests for linkedin/connections_store.py, including the file-locking fix
(2026-08-10, PRD S13 #25, Zahir's whole-codebase adversarial self-audit
request). No storage-layer tests existed for this module before this
file. Also closes a real gap found while writing these: tests/conftest.py's
isolated_data fixture isolated linkedin/storage.py's LINKEDIN_PATH but
never connections_store.py's CONNECTIONS_PATH - any test exercising
save_connections() would have silently written the real
data/linkedin/connections.json (PII about Zahir's real contacts).
"""

import threading

import linkedin.connections_store as connections_store


class RecordingLock:
    """Stand-in for security.file_lock.locked() - same pattern as
    test_linkedin_storage_lock.py/test_outreach.py/test_target_accounts.py."""

    def __init__(self, calls, name):
        self.calls = calls
        self.name = name

    def __enter__(self):
        self.calls.append(("enter", self.name))
        return self

    def __exit__(self, *exc):
        self.calls.append(("exit", self.name))
        return False


def test_load_defaults_when_nothing_saved_yet(isolated_data):
    snapshot = connections_store.load_connections_snapshot()
    assert snapshot == {"connections": [], "source_file": None, "last_saved": None}


def test_save_then_reload_round_trips(isolated_data):
    connections = [{"first_name": "Jane", "last_name": "Doe", "company": "Acme"}]
    connections_store.save_connections(connections, "Connections.csv", "2026-08-10T00:00:00+00:00")
    snapshot = connections_store.load_connections_snapshot()
    assert snapshot["connections"] == connections
    assert snapshot["source_file"] == "Connections.csv"
    assert snapshot["last_saved"] == "2026-08-10T00:00:00+00:00"


def test_save_connections_is_a_full_replace_not_a_merge(isolated_data):
    """Module docstring: "Full replace on each upload... connections data
    goes stale, there's no reason to keep merging old rows." """
    connections_store.save_connections([{"first_name": "Old"}], "first.csv", "2026-08-01T00:00:00+00:00")
    connections_store.save_connections([{"first_name": "New"}], "second.csv", "2026-08-10T00:00:00+00:00")
    snapshot = connections_store.load_connections_snapshot()
    assert snapshot["connections"] == [{"first_name": "New"}]
    assert snapshot["source_file"] == "second.csv"


def test_save_connections_runs_inside_its_own_lock_name(isolated_data, monkeypatch):
    """A genuinely separate file (CONNECTIONS_PATH, not LINKEDIN_PATH) -
    gets its own lock name rather than sharing linkedin.storage's, same
    "each store gets its own lock" rule as target_accounts.py's
    website_lookup_cost split."""
    calls = []
    monkeypatch.setattr(connections_store, "locked", lambda name: RecordingLock(calls, name))
    connections_store.save_connections([{"first_name": "Jane"}], "Connections.csv", "2026-08-10T00:00:00+00:00")
    assert calls == [("enter", "linkedin_connections"), ("exit", "linkedin_connections")]


def test_concurrent_save_connections_does_not_corrupt_the_file(isolated_data):
    """Real proof against the actual msvcrt lock (not a mock): 8 threads
    each replacing the whole snapshot concurrently - the lock's job here
    isn't preserving every write (full-replace means the last one wins by
    design), it's making sure concurrent writes never interleave into a
    corrupted/unreadable file, same real failure mode the outreach.json
    bug hit (a torn write that failed to decrypt on the next read)."""
    threads_n = 8

    def worker(i):
        connections_store.save_connections(
            [{"first_name": f"Contact {i}"}], f"file{i}.csv", "2026-08-10T00:00:00+00:00",
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a thread hung - possible deadlock in locked()"

    # No exception reading it back proves the file wasn't left corrupted
    # mid-write by two overlapping writers - whichever write actually
    # "won" is a valid, fully-formed snapshot either way.
    snapshot = connections_store.load_connections_snapshot()
    assert snapshot["source_file"] is not None
    assert snapshot["source_file"].startswith("file")
