import prospector.target_accounts as target_accounts


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
