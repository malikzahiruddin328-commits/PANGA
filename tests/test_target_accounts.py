import threading
from datetime import datetime, timedelta, timezone

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


# ---- status_updated_at (2026-08-10, PRD S16a #20) ----
#
# Prerequisite for #15/#16 below - both need a "did Zahir already know
# about this when he made the call" reference point, which only exists if
# every status change (manual or automatic) actually stamps one.

def test_add_signal_sets_status_updated_at_on_new_account(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    account = target_accounts.get_target_account("Aerospike")
    assert account["status_updated_at"] is not None


def test_set_status_bumps_status_updated_at_only_on_real_change(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    target_accounts.set_status("Aerospike", "disqualified")
    first_stamp = target_accounts.get_target_account("Aerospike")["status_updated_at"]

    target_accounts.set_status("Aerospike", "disqualified", notes="same status, just adding a note")
    assert target_accounts.get_target_account("Aerospike")["status_updated_at"] == first_stamp

    target_accounts.set_status("Aerospike", "stale")
    assert target_accounts.get_target_account("Aerospike")["status_updated_at"] != first_stamp


def test_recompute_status_bumps_status_updated_at_on_auto_transition(isolated_data):
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial", ref="NCT1")
    first_stamp = target_accounts.get_target_account("Aerospike")["status_updated_at"]

    # A second signal of the SAME type doesn't change status - shouldn't bump.
    target_accounts.add_signal("Aerospike", "late_stage_trial", "clinicaltrials.gov", "trial 2", ref="NCT2")
    assert target_accounts.get_target_account("Aerospike")["status_updated_at"] == first_stamp

    # A distinct second type promotes watching -> qualified - a real
    # automatic transition, should bump just like a manual one does.
    target_accounts.add_signal("Aerospike", "commercial_hiring", "jobs", "posting", ref="job1")
    account = target_accounts.get_target_account("Aerospike")
    assert account["status"] == "qualified"
    assert account["status_updated_at"] != first_stamp


# ---- find_paused_accounts_with_new_activity (2026-08-09, hardened 2026-08-10) ----

def _job(source="linkedin", job_id="1", org="Acme Bio", title="VP Commercial"):
    return {"source": source, "job_id": job_id, "organization": org, "title": title}


def test_finds_paused_account_with_matching_job_not_in_its_own_signals(isolated_data):
    target_accounts.add_signal("Acme Bio", "regulatory_filing", "openfda", "approved drug", ref="NDA1")
    target_accounts.set_status("Acme Bio", "disqualified")

    flags = target_accounts.find_paused_accounts_with_new_activity(
        target_accounts.load_target_accounts(), [_job()], [],
    )
    assert len(flags) == 1
    assert flags[0]["company_name"] == "Acme Bio"
    assert flags[0]["status"] == "disqualified"
    assert len(flags[0]["matching_jobs"]) == 1


def test_excludes_match_when_job_ref_already_a_known_signal(isolated_data):
    """Securitas-style false positive: a company disqualified BECAUSE of a
    specific posting must not re-flag itself forever on that exact same
    posting."""
    target_accounts.add_signal("Acme Bio", "commercial_hiring", "jobs", "posting", ref="linkedin:1")
    target_accounts.set_status("Acme Bio", "disqualified", notes="wrong industry")

    flags = target_accounts.find_paused_accounts_with_new_activity(
        target_accounts.load_target_accounts(), [_job(source="linkedin", job_id="1")], [],
    )
    assert flags == []


def test_stale_status_is_covered(isolated_data):
    target_accounts.add_signal("Acme Bio", "regulatory_filing", "openfda", "approved drug", ref="NDA1")
    target_accounts.set_status("Acme Bio", "stale")

    flags = target_accounts.find_paused_accounts_with_new_activity(
        target_accounts.load_target_accounts(), [_job()], [],
    )
    assert len(flags) == 1
    assert flags[0]["status"] == "stale"


def test_contacted_status_is_not_covered(isolated_data):
    """"contacted" means Zahir is actively engaged, not paused - a
    different situation this check isn't meant to nudge."""
    target_accounts.add_signal("Acme Bio", "regulatory_filing", "openfda", "approved drug", ref="NDA1")
    target_accounts.set_status("Acme Bio", "contacted")

    flags = target_accounts.find_paused_accounts_with_new_activity(
        target_accounts.load_target_accounts(), [_job()], [],
    )
    assert flags == []


def test_qualified_and_watching_accounts_never_included(isolated_data):
    target_accounts.add_signal("Acme Bio", "regulatory_filing", "openfda", "approved drug", ref="NDA1")
    # status is "watching" here - never manually paused
    flags = target_accounts.find_paused_accounts_with_new_activity(
        target_accounts.load_target_accounts(), [_job()], [],
    )
    assert flags == []


def test_application_status_attached_when_a_matching_application_exists(isolated_data):
    target_accounts.add_signal("Acme Bio", "regulatory_filing", "openfda", "approved drug", ref="NDA1")
    target_accounts.set_status("Acme Bio", "disqualified")

    flags = target_accounts.find_paused_accounts_with_new_activity(
        target_accounts.load_target_accounts(), [_job()],
        [{"source": "linkedin", "job_id": "1", "status": "under review"}],
    )
    assert flags[0]["matching_jobs"][0]["application_status"] == "under review"


def test_new_signal_after_status_change_is_flagged(isolated_data):
    """PRD S16a #15: a signal added to the account's OWN signals list after
    it was paused is exactly the same "new evidence" shape as a new job
    posting - must be flagged the same way."""
    target_accounts.add_signal("Acme Bio", "regulatory_filing", "openfda", "approved drug", ref="NDA1")
    target_accounts.set_status("Acme Bio", "disqualified")

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    target_accounts.add_signal(
        "Acme Bio", "late_stage_trial", "clinicaltrials.gov", "new trial", ref="NCT99", date_observed=future,
    )

    flags = target_accounts.find_paused_accounts_with_new_activity(
        target_accounts.load_target_accounts(), [], [],
    )
    assert len(flags) == 1
    assert len(flags[0]["new_signals"]) == 1
    assert flags[0]["new_signals"][0]["ref"] == "NCT99"
    assert flags[0]["matching_jobs"] == []


def test_signal_before_status_change_is_not_flagged(isolated_data):
    """The signal that was part of the ORIGINAL evidence for a disqualify
    decision must not be re-flagged as "new" - only genuinely new signals
    arriving after the decision count."""
    target_accounts.add_signal("Acme Bio", "regulatory_filing", "openfda", "approved drug", ref="NDA1")
    target_accounts.set_status("Acme Bio", "disqualified")  # decision made AFTER the signal above

    flags = target_accounts.find_paused_accounts_with_new_activity(
        target_accounts.load_target_accounts(), [], [],
    )
    assert flags == []


def test_no_status_updated_at_skips_new_signal_check_but_not_job_check(isolated_data):
    """Legacy accounts (paused before status_updated_at existed) have no
    before/after reference point - the new-signal check can't run for
    them (excluded, not guessed at), but the job-posting check doesn't
    depend on this field at all and must keep working."""
    target_accounts.add_signal("Acme Bio", "regulatory_filing", "openfda", "approved drug", ref="NDA1")
    target_accounts.set_status("Acme Bio", "disqualified")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    target_accounts.add_signal(
        "Acme Bio", "late_stage_trial", "clinicaltrials.gov", "new trial", ref="NCT99", date_observed=future,
    )

    # Simulate a legacy record: strip status_updated_at directly, bypassing
    # the normal API (this field simply didn't exist before 2026-08-10).
    accounts = target_accounts.load_target_accounts()
    del accounts[0]["status_updated_at"]
    target_accounts._save_all(accounts)

    flags = target_accounts.find_paused_accounts_with_new_activity(
        target_accounts.load_target_accounts(), [_job()], [],
    )
    assert len(flags) == 1
    assert flags[0]["new_signals"] == []  # can't tell "new" without a cutoff
    assert len(flags[0]["matching_jobs"]) == 1  # unaffected by the missing field


def test_empty_inputs_return_empty_list(isolated_data):
    assert target_accounts.find_paused_accounts_with_new_activity([], [], []) == []


def test_normalize_called_once_per_job_not_once_per_paused_account(isolated_data, monkeypatch):
    """PRD S16a #10: the original version re-normalized every job's
    organization name for EVERY paused account (O(paused x jobs) calls to
    _normalize_company for the job side alone) - measured as the dominant
    real cost, ~675ms at real data volume. Proven here structurally
    (call-count), not by timing (timing-based assertions are flaky across
    hardware) - job_index must be built once, not once per account."""
    real_normalize = target_accounts._normalize_company
    calls = []

    def counting_normalize(name):
        calls.append(name)
        return real_normalize(name)

    monkeypatch.setattr(target_accounts, "_normalize_company", counting_normalize)

    for i in range(3):
        target_accounts.add_signal(f"Company {i}", "regulatory_filing", "openfda", "approved drug", ref=f"NDA{i}")
        target_accounts.set_status(f"Company {i}", "disqualified")

    calls.clear()  # only count calls made during the check itself
    jobs = [_job(job_id=str(i), org=f"Unrelated Co {i}") for i in range(50)]
    target_accounts.find_paused_accounts_with_new_activity(target_accounts.load_target_accounts(), jobs, [])

    # 50 jobs + 3 accounts = 53 calls total if normalized once each: the
    # unoptimized version would have made 3 (accounts) x 50 (jobs) = 150+
    # calls just for the job side alone.
    assert len(calls) <= len(jobs) + 3


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
