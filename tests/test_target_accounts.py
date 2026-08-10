import threading

import prospector.target_accounts as target_accounts


class RecordingLock:
    """Stand-in for security.file_lock.locked() that records whether it
    was held while the save actually happened, without touching a real
    lock file - same pattern as tests/test_gmail_client.py (commit
    81259f8) and tests/test_outreach.py (commit 57d94ee)."""

    def __init__(self, calls, name):
        self.calls = calls
        self.name = name

    def __enter__(self):
        self.calls.append(("enter", self.name))
        return self

    def __exit__(self, *exc):
        self.calls.append(("exit", self.name))
        return False


def test_add_signal_creates_account_as_watching(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "NCT123", ref="NCT123")
    accounts = target_accounts.load_target_accounts()
    assert len(accounts) == 1
    assert accounts[0]["status"] == "watching"
    assert len(accounts[0]["signals"]) == 1


def test_two_distinct_signal_types_promotes_to_qualified(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    target_accounts.add_signal("Aerospike", "commercial_hiring", "jobs", "posting", ref="job1")
    account = target_accounts.get_target_account("Aerospike")
    assert account["status"] == "qualified"


def test_two_signals_of_the_same_type_stays_watching(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial 1", ref="NCT1")
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial 2", ref="NCT2")
    account = target_accounts.get_target_account("Aerospike")
    assert account["status"] == "watching"
    assert len(account["signals"]) == 2


def test_add_signal_dedupes_by_ref(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial (reworded)", ref="NCT1")
    account = target_accounts.get_target_account("Aerospike")
    assert len(account["signals"]) == 1


def test_manual_status_is_sticky_against_new_signals(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    target_accounts.set_status("Aerospike", "disqualified", notes="wrong industry")
    # A new, distinct-type signal arrives later - must NOT silently
    # override a status Zahir set himself.
    target_accounts.add_signal("Aerospike", "commercial_hiring", "jobs", "posting", ref="job1")
    account = target_accounts.get_target_account("Aerospike")
    assert account["status"] == "disqualified"


def test_set_status_is_case_insensitive_on_company_name(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    target_accounts.set_status("AEROSPIKE", "contacted")
    assert target_accounts.get_target_account("Aerospike")["status"] == "contacted"


def test_set_website_caches_url(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    target_accounts.set_website("Aerospike", "https://www.aerospike.com")
    account = target_accounts.get_target_account("Aerospike")
    assert account["website"] == "https://www.aerospike.com"


def test_website_lookup_cost_defaults_to_zero(isolated_data):
    record = target_accounts.load_website_lookup_cost()
    assert record["cost"] == 0.0
    assert record["count"] == 0
    assert record["at"] is None


def test_save_and_reload_website_lookup_cost(isolated_data):
    target_accounts.save_website_lookup_cost(3.18, 36)
    record = target_accounts.load_website_lookup_cost()
    assert record["cost"] == 3.18
    assert record["count"] == 36
    assert record["at"] is not None


# ---- locking fix (2026-08-09) ----
#
# Found by re-auditing target_accounts.py through the same lens as the
# outreach.json fix (57d94ee): 7 write call-sites, zero locking, despite
# a real concurrent-writer exposure - add_signal() gets called from live
# Claude Code reasoning sessions populating signals while the Streamlit
# dashboard (which Zahir routinely has open) can call set_status()/
# set_website() from his own clicks at the same time.

def test_add_signal_save_runs_inside_the_lock(isolated_data, monkeypatch):
    calls = []
    monkeypatch.setattr(target_accounts, "locked", lambda name: RecordingLock(calls, name))
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    assert calls[0] == ("enter", "target_accounts")
    assert calls[-1] == ("exit", "target_accounts")


def test_set_status_runs_inside_the_lock(isolated_data, monkeypatch):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    calls = []
    monkeypatch.setattr(target_accounts, "locked", lambda name: RecordingLock(calls, name))
    target_accounts.set_status("Aerospike", "disqualified")
    assert calls[0] == ("enter", "target_accounts")
    assert calls[-1] == ("exit", "target_accounts")


def test_set_website_runs_inside_the_lock(isolated_data, monkeypatch):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    calls = []
    monkeypatch.setattr(target_accounts, "locked", lambda name: RecordingLock(calls, name))
    target_accounts.set_website("Aerospike", "https://www.aerospike.com")
    assert calls[0] == ("enter", "target_accounts")
    assert calls[-1] == ("exit", "target_accounts")


def test_save_website_lookup_cost_uses_its_own_lock_name(isolated_data, monkeypatch):
    """A genuinely separate file (WEBSITE_LOOKUP_COST_PATH, not
    TARGET_ACCOUNTS_PATH) gets its own lock name - locking target_accounts
    for an unrelated file would serialize operations that don't actually
    conflict, same "each store gets its own lock" rule file_lock.py
    documents."""
    calls = []
    monkeypatch.setattr(target_accounts, "locked", lambda name: RecordingLock(calls, name))
    target_accounts.save_website_lookup_cost(3.18, 36)
    assert calls == [("enter", "website_lookup_cost"), ("exit", "website_lookup_cost")]


def test_concurrent_add_signal_loses_no_signals(isolated_data):
    """Real proof against the actual msvcrt lock (not a mock): 8 threads
    each adding a distinct signal to the SAME company concurrently - if
    the lock isn't actually serializing these read-modify-write calls,
    some signals silently overwrite each other's append and the final
    count comes up short, same failure mode the outreach.json bug had."""
    threads_n = 8

    def worker(i):
        target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", f"trial {i}", ref=f"NCT{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a thread hung - possible deadlock in locked()"

    account = target_accounts.get_target_account("Aerospike")
    assert len(account["signals"]) == threads_n
    assert len({s["ref"] for s in account["signals"]}) == threads_n
