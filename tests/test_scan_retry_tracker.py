"""Tests for src/scan_retry_tracker.py's bounded-retry counting (2026-08-09) -
see that module's docstring for the full "why" (a message must eventually
stop being retried, but a single transient failure shouldn't cost it its
one shot)."""

import scan_retry_tracker as tracker


def test_record_failure_increments_and_returns_count(isolated_data):
    assert tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1") == 1
    assert tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1") == 2
    assert tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1") == 3


def test_record_failure_is_independent_per_message(isolated_data):
    tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1")
    tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1")
    assert tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t2") == 1  # separate message, separate count


def test_record_failure_is_independent_per_scan_name(isolated_data):
    # Same underlying message, but gmail_cta_scan and job_alert_scan run
    # different reasoning calls over it - a failure in one says nothing
    # about the other, so their counts must not share a budget.
    tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1")
    tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1")
    assert tracker.record_failure("job_alert_scan", "gmail", "gmail", "t1") == 1


def test_record_failure_is_independent_per_account(isolated_data):
    tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1")
    tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1")
    assert tracker.record_failure("gmail_cta_scan", "imap", "me@yahoo.com", "t1") == 1  # same ref, different account


def test_clear_failure_resets_the_count(isolated_data):
    tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1")
    tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1")
    tracker.clear_failure("gmail_cta_scan", "gmail", "gmail", "t1")
    assert tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1") == 1  # started over, not continuing from 2


def test_clear_failure_is_a_no_op_when_nothing_was_recorded(isolated_data):
    tracker.clear_failure("gmail_cta_scan", "gmail", "gmail", "never-failed")  # must not raise
    assert tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "never-failed") == 1


def test_max_attempts_reaches_give_up_threshold(isolated_data):
    counts = [tracker.record_failure("gmail_cta_scan", "gmail", "gmail", "t1") for _ in range(tracker.MAX_ATTEMPTS)]
    assert counts[-1] >= tracker.MAX_ATTEMPTS
