"""Real gap found 2026-08-10 (Zahir's adversarial self-audit request, PRD
S17 #22): the Learn Engine's "nothing to analyze yet" gate
(src/ui/app.py, "Run Learn Engine analysis" button) used to sum raw list
lengths across all four learn_input categories. target_account_vs_outcome
(learn_engine.gather_learn_engine_input) appends ONE ENTRY PER TARGET
ACCOUNT UNCONDITIONALLY, not just ones with a real correlatable outcome -
with 78 real target accounts already in the store, that alone always
pushed the raw sum past zero, so the gate could never correctly fire
again once Prospector had been used at all, regardless of whether any of
the other three categories had real signal in them.

Fixed with _count_analyzeable_learn_inputs(): only a target_account_vs_
outcome entry with real_posting_appeared_since=True counts - a "still
watching, nothing has happened yet" account has no real outcome to
correlate its qualification against.
"""

import prospector.target_accounts as target_accounts


def _learn_input(scoring=None, target_accounts_list=None, outreach_list=None, interview=None):
    return {
        "scoring_vs_outcome": scoring or [],
        "target_account_vs_outcome": target_accounts_list or [],
        "outreach_vs_outcome": outreach_list or [],
        "interview_outcomes": interview or [],
    }


def test_all_empty_counts_zero(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    import ui.app as app_module

    assert app_module._count_analyzeable_learn_inputs(_learn_input()) == 0


def test_target_accounts_with_no_real_posting_do_not_count(isolated_data, monkeypatch):
    """The exact bug: accounts that are still just "watching" (no real
    posting has appeared to validate the qualification signal) used to
    inflate the count via raw len() even though there's genuinely nothing
    to learn from them yet."""
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    import ui.app as app_module

    learn_input = _learn_input(target_accounts_list=[
        {"company_name": "Acme", "real_posting_appeared_since": False},
        {"company_name": "Widget Co", "real_posting_appeared_since": False},
    ])
    assert app_module._count_analyzeable_learn_inputs(learn_input) == 0


def test_target_account_with_real_posting_does_count(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    import ui.app as app_module

    learn_input = _learn_input(target_accounts_list=[
        {"company_name": "Acme", "real_posting_appeared_since": False},
        {"company_name": "Widget Co", "real_posting_appeared_since": True},
    ])
    assert app_module._count_analyzeable_learn_inputs(learn_input) == 1


def test_the_other_three_categories_still_count_by_raw_length(isolated_data, monkeypatch):
    """Only target_account_vs_outcome had the unconditional-append issue -
    the other three are already conditionally populated in learn_engine.py
    and should keep counting normally."""
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    import ui.app as app_module

    learn_input = _learn_input(
        scoring=[{"title": "CIO"}],
        outreach_list=[{"channel": "email"}],
        interview=[{"outcome": "went well"}],
    )
    assert app_module._count_analyzeable_learn_inputs(learn_input) == 3


def test_real_world_scale_78_watching_accounts_zero_real_signal(isolated_data, monkeypatch):
    """Reproduces the exact real-data scenario Zahir's audit named: 78
    target accounts, all still watching/disqualified with no real
    posting - before this fix, the gate would have incorrectly let the
    "Run Learn Engine analysis" button proceed to a real, paid API call
    with nothing genuinely analyzeable behind it."""
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    import ui.app as app_module
    from prospector.learn_engine import gather_learn_engine_input

    for i in range(78):
        target_accounts.add_signal(f"Company {i}", "regulatory_filing", "openfda", "approved drug", ref=f"NDA{i}")
    # No jobs, no applications, no outreach, no interview outcomes - only
    # target accounts exist, none with a real posting to validate against.

    learn_input = gather_learn_engine_input([], [], target_accounts.load_target_accounts(), [], [])
    assert len(learn_input["target_account_vs_outcome"]) == 78  # the raw list is indeed 78 long...
    assert app_module._count_analyzeable_learn_inputs(learn_input) == 0  # ...but nothing analyzeable
