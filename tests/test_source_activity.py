import pytest

import search.source_activity as source_activity


@pytest.fixture
def isolated_activity(tmp_path, monkeypatch):
    monkeypatch.setattr(source_activity, "ACTIVITY_PATH", tmp_path / "source_activity.json")
    return tmp_path


def test_is_source_stale_returns_none_when_no_history(isolated_activity):
    assert source_activity.is_source_stale("Rigzone") is None


def test_is_source_stale_returns_none_before_enough_real_runs(isolated_activity):
    for _ in range(source_activity.MIN_CONSECUTIVE_ZERO_RUNS - 1):
        source_activity.record_run_result("Rigzone", 0)
    assert source_activity.is_source_stale("Rigzone") is None


def test_is_source_stale_true_after_enough_consecutive_zero_runs(isolated_activity):
    for _ in range(source_activity.MIN_CONSECUTIVE_ZERO_RUNS):
        source_activity.record_run_result("Rigzone", 0)
    assert source_activity.is_source_stale("Rigzone") is True


def test_is_source_stale_false_when_a_recent_run_added_something(isolated_activity):
    for _ in range(source_activity.MIN_CONSECUTIVE_ZERO_RUNS - 1):
        source_activity.record_run_result("Rigzone", 0)
    source_activity.record_run_result("Rigzone", 3)
    assert source_activity.is_source_stale("Rigzone") is False


def test_errored_runs_are_not_evidence_of_staleness(isolated_activity):
    # A source that's actually fine but had a bad-network day shouldn't get
    # flagged just because an errored run's added=0 slips into the streak.
    for _ in range(source_activity.MIN_CONSECUTIVE_ZERO_RUNS):
        source_activity.record_run_result("Rigzone", 0)
    source_activity.record_run_result("Rigzone", 0, had_error=True)
    source_activity.record_run_result("Rigzone", 0, had_error=True)
    # still exactly MIN_CONSECUTIVE_ZERO_RUNS real zero-runs - still stale
    assert source_activity.is_source_stale("Rigzone") is True


def test_errored_runs_do_not_grant_a_free_pass_from_staleness(isolated_activity):
    # A genuinely dead source shouldn't read as "unknown"/healthy just
    # because one check happened to error out instead of returning zero.
    for _ in range(source_activity.MIN_CONSECUTIVE_ZERO_RUNS - 1):
        source_activity.record_run_result("Rigzone", 0)
    source_activity.record_run_result("Rigzone", 0, had_error=True)
    # only MIN_CONSECUTIVE_ZERO_RUNS - 1 real runs recorded - not enough yet
    assert source_activity.is_source_stale("Rigzone") is None


def test_history_is_bounded_to_max_history_per_source(isolated_activity):
    for i in range(source_activity.MAX_HISTORY_PER_SOURCE + 10):
        source_activity.record_run_result("Dice", 1)
    data = source_activity._load()
    assert len(data["Dice"]) == source_activity.MAX_HISTORY_PER_SOURCE


def test_sources_are_tracked_independently(isolated_activity):
    for _ in range(source_activity.MIN_CONSECUTIVE_ZERO_RUNS):
        source_activity.record_run_result("Rigzone", 0)
        source_activity.record_run_result("Built In", 5)
    assert source_activity.is_source_stale("Rigzone") is True
    assert source_activity.is_source_stale("Built In") is False


def test_all_tracked_sources_lists_every_recorded_source(isolated_activity):
    source_activity.record_run_result("Dice", 1)
    source_activity.record_run_result("Built In", 2)
    assert source_activity.all_tracked_sources() == ["Built In", "Dice"]
