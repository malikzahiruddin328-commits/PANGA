from datetime import date

from prospector.clinical_trials import _is_recent_enough, normalize_trial_signals

# Real bug found live 2026-07-31: this module originally had NO recency
# filter at all - a Phase 3 trial completed in 2003 passed exactly like one
# from last month, as long as the sponsor cleared company_filters. These
# tests guard the fix (STALE_YEARS recency filter) directly against
# regressing back to that state.


def _months_ago(n: int) -> str:
    today = date.today()
    total = today.year * 12 + (today.month - 1) - n
    year, month = divmod(total, 12)
    return f"{year}-{month + 1:02d}"


def test_active_not_recruiting_always_recent_regardless_of_date():
    assert _is_recent_enough({"status": "ACTIVE_NOT_RECRUITING", "primary_completion_date": None})
    assert _is_recent_enough({"status": "ACTIVE_NOT_RECRUITING", "primary_completion_date": "2003-01"})


def test_completed_trial_within_stale_window_is_recent():
    assert _is_recent_enough({"status": "COMPLETED", "primary_completion_date": _months_ago(6)})


def test_completed_trial_older_than_stale_window_is_not_recent():
    assert not _is_recent_enough({"status": "COMPLETED", "primary_completion_date": "2003-07"})
    assert not _is_recent_enough({"status": "COMPLETED", "primary_completion_date": _months_ago(30)})


def test_completed_trial_with_no_date_at_all_is_not_recent():
    # Unverifiable recency - the real AlgoRx Pharmaceuticals bug (a
    # COMPLETED trial with no completion date listed at all) must stay
    # excluded rather than silently passing through.
    assert not _is_recent_enough({"status": "COMPLETED", "primary_completion_date": None})


def test_malformed_date_is_not_recent():
    assert not _is_recent_enough({"status": "COMPLETED", "primary_completion_date": "not listed"})


def test_normalize_trial_signals_excludes_stale_trials():
    trials = [
        {
            "sponsor": "Pacira Pharmaceuticals, Inc",
            "nct_id": "NCT02357459",
            "status": "COMPLETED",
            "primary_completion_date": "2016-01",
            "conditions": ["Osteoarthritis"],
        },
        {
            "sponsor": "Kailera Therapeutics, Inc.",
            "nct_id": "NCT99999999",
            "status": "COMPLETED",
            "primary_completion_date": _months_ago(3),
            "conditions": ["Obesity"],
        },
    ]
    signals = normalize_trial_signals(trials)
    sponsors = {s["company_name"] for s in signals}
    assert sponsors == {"Kailera Therapeutics, Inc."}


def test_normalize_trial_signals_excludes_non_company_sponsors():
    trials = [{
        "sponsor": "Radiation Therapy Oncology Group",
        "nct_id": "NCT00003162",
        "status": "ACTIVE_NOT_RECRUITING",
        "conditions": ["Breast Cancer"],
    }]
    assert normalize_trial_signals(trials) == []
