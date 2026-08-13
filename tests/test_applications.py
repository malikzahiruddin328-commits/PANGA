import time

import tailoring.applications as applications


def test_upsert_application_creates_new_record(isolated_data):
    applications.upsert_application("Dice", "1", status="under review")
    app = applications.get_application("Dice", "1")
    assert app["status"] == "under review"
    assert app["created_at"]
    assert app["status_updated_at"]


def test_upsert_application_none_fields_do_not_overwrite_existing_values(isolated_data):
    applications.upsert_application("Dice", "1", status="under review", resume_text="draft v1")
    applications.upsert_application("Dice", "1", status="under review", resume_text=None)
    app = applications.get_application("Dice", "1")
    # resume_text=None on the second call must NOT blank out the first draft.
    assert app["resume_text"] == "draft v1"


def test_upsert_application_explicit_value_does_overwrite(isolated_data):
    applications.upsert_application("Dice", "1", status="under review", resume_text="draft v1")
    applications.upsert_application("Dice", "1", status="under review", resume_text="draft v2")
    assert applications.get_application("Dice", "1")["resume_text"] == "draft v2"


def test_upsert_application_status_updated_at_only_bumps_on_real_change(isolated_data):
    applications.upsert_application("Dice", "1", status="under review")
    first_stamp = applications.get_application("Dice", "1")["status_updated_at"]
    applications.upsert_application("Dice", "1", status="under review")
    assert applications.get_application("Dice", "1")["status_updated_at"] == first_stamp


def test_documents_drafted_at_set_when_any_prose_field_passed(isolated_data):
    applications.upsert_application("Dice", "1", status="under review", cover_letter_text="draft")
    assert applications.get_application("Dice", "1")["documents_drafted_at"] is not None


def test_apply_answers_empty_list_clears_while_none_leaves_untouched(isolated_data):
    applications.upsert_application("Dice", "1", status="under review", apply_answers=[{"label": "Phone", "value": "555"}])
    applications.upsert_application("Dice", "1", status="under review", apply_answers=[])
    assert applications.get_application("Dice", "1")["apply_answers"] == []


def test_needs_edit_review_false_when_no_documents_drafted(isolated_data):
    applications.upsert_application("Dice", "1", status="under review")
    app = applications.get_application("Dice", "1")
    assert applications.needs_edit_review(app) is False


def test_needs_edit_review_true_when_drafted_but_never_reviewed(isolated_data):
    applications.upsert_application("Dice", "1", status="under review", resume_text="draft")
    app = applications.get_application("Dice", "1")
    assert applications.needs_edit_review(app) is True


def test_needs_edit_review_false_after_review_with_a_real_reason(isolated_data):
    applications.upsert_application("Dice", "1", status="under review", resume_text="draft")
    applications.record_document_edit_review("Dice", "1", {"resume": {"changed": False, "diff": []}}, "No changes made.")
    app = applications.get_application("Dice", "1")
    assert applications.needs_edit_review(app) is False


def test_needs_edit_review_true_again_after_regenerate_invalidates_stale_review(isolated_data):
    applications.upsert_application("Dice", "1", status="under review", resume_text="draft v1")
    time.sleep(0.01)  # force distinct timestamps - the gate is an ordering check
    applications.record_document_edit_review("Dice", "1", {"resume": {"changed": False, "diff": []}}, "No changes made.")
    time.sleep(0.01)
    # Regenerating bumps documents_drafted_at past the saved review's
    # checked_at - the old review must no longer count as satisfying the gate.
    applications.upsert_application("Dice", "1", status="under review", resume_text="draft v2")
    app = applications.get_application("Dice", "1")
    assert applications.needs_edit_review(app) is True


def test_needs_edit_review_true_with_blank_reason(isolated_data):
    applications.upsert_application("Dice", "1", status="under review", resume_text="draft")
    applications.record_document_edit_review("Dice", "1", {}, "   ")
    app = applications.get_application("Dice", "1")
    assert applications.needs_edit_review(app) is True


def test_set_strategy_tag(isolated_data):
    applications.upsert_application("Dice", "1", status="under review")
    applications.set_strategy_tag("Dice", "1", "concise-2-page-ats-focused")
    assert applications.get_application("Dice", "1")["strategy_tag"] == "concise-2-page-ats-focused"


def test_suggested_strategy_tag_stored_separately_from_real_tag(isolated_data):
    applications.upsert_application("Dice", "1", status="under review", suggested_strategy_tag="concise-2-page")
    app = applications.get_application("Dice", "1")
    assert app["strategy_tag_suggestion"] == "concise-2-page"
    assert app.get("strategy_tag") is None

    # Zahir saves his own real tag - a later regenerate's suggestion must
    # never silently overwrite it (the UI only prefills from the
    # suggestion when strategy_tag is still empty).
    applications.set_strategy_tag("Dice", "1", "his-own-tag")
    applications.upsert_application("Dice", "1", status="under review", suggested_strategy_tag="a-new-suggestion")
    app = applications.get_application("Dice", "1")
    assert app["strategy_tag"] == "his-own-tag"
    assert app["strategy_tag_suggestion"] == "a-new-suggestion"


def test_get_applications_with_open_clarifying_questions_filters_to_non_empty(isolated_data):
    applications.upsert_application(
        "Dice", "1", status="under review",
        resume_clarifying_questions=[{"skill": "SQL", "type": "skill_gap", "question": "?", "suggested_answer": ""}],
    )
    applications.upsert_application("Dice", "2", status="under review", resume_clarifying_questions=[])
    applications.upsert_application("Dice", "3", status="under review")

    open_apps = applications.get_applications_with_open_clarifying_questions()
    job_ids = {a["job_id"] for a in open_apps}
    assert job_ids == {"1"}


def test_get_applications_with_open_clarifying_questions_drops_off_after_regenerate_clears_them(isolated_data):
    applications.upsert_application(
        "Dice", "1", status="under review",
        resume_clarifying_questions=[{"skill": "SQL", "type": "skill_gap", "question": "?", "suggested_answer": ""}],
    )
    assert len(applications.get_applications_with_open_clarifying_questions()) == 1

    # A regenerate that maxes the score (or otherwise closes every gap)
    # persists an empty list - the job must disappear from this list, not
    # linger with stale questions.
    applications.upsert_application("Dice", "1", status="under review", resume_clarifying_questions=[])
    assert applications.get_applications_with_open_clarifying_questions() == []


def test_try_acquire_generation_lock_succeeds_when_no_application_record_exists_yet(isolated_data):
    # Real case: the very first Generate click on a job that's never had
    # an application record created at all.
    assert applications.try_acquire_generation_lock("Dice", "1") is True
    app = applications.get_application("Dice", "1")
    assert app["generation_lock_acquired_at"]
    assert app["status"] == "under review"


def test_try_acquire_generation_lock_fails_while_genuinely_held(isolated_data):
    # The real scenario this guards against: two browser tabs (or a fast
    # double-click) both hitting Generate on the same job close together.
    assert applications.try_acquire_generation_lock("Dice", "1") is True
    assert applications.try_acquire_generation_lock("Dice", "1") is False


def test_try_acquire_generation_lock_succeeds_again_after_release(isolated_data):
    assert applications.try_acquire_generation_lock("Dice", "1") is True
    applications.release_generation_lock("Dice", "1")
    assert applications.try_acquire_generation_lock("Dice", "1") is True


def test_try_acquire_generation_lock_does_not_block_a_different_job(isolated_data):
    assert applications.try_acquire_generation_lock("Dice", "1") is True
    assert applications.try_acquire_generation_lock("Dice", "2") is True


def test_release_generation_lock_is_a_safe_no_op_when_nothing_was_held(isolated_data):
    # A finally block always calls this, even on a path that returned
    # early before ever acquiring - must never raise on an already-clear
    # or nonexistent lock.
    applications.release_generation_lock("Dice", "1")  # no application record at all
    applications.upsert_application("Dice", "2", status="under review")
    applications.release_generation_lock("Dice", "2")  # record exists, lock never held


def test_try_acquire_generation_lock_recovers_from_a_stale_lock(isolated_data, monkeypatch):
    # Real scenario CLAUDE.md's own "check for locking errors...unhandled
    # exceptions that could leave a lock held" concern names directly: a
    # process crashes or the connection drops mid-draft, after acquiring
    # the lock but before its finally block ever runs release_generation_
    # lock(). Without a staleness ceiling, that job would be locked out of
    # Generate forever.
    import tailoring.applications as applications_module
    from datetime import datetime, timedelta, timezone

    assert applications.try_acquire_generation_lock("Dice", "1") is True

    # Simulate that acquisition happening well past the staleness ceiling.
    apps = applications.load_applications()
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=applications_module._GENERATION_LOCK_STALE_AFTER_MINUTES + 1)
    for app in apps:
        if app["source"] == "Dice" and app["job_id"] == "1":
            app["generation_lock_acquired_at"] = stale_time.isoformat()
    applications._save_all(apps)

    assert applications.try_acquire_generation_lock("Dice", "1") is True


def test_try_acquire_generation_lock_still_blocks_a_recent_lock(isolated_data):
    # Sanity check for the staleness test above - a lock well within the
    # ceiling must still block, not just any timestamped lock.
    import tailoring.applications as applications_module
    from datetime import datetime, timedelta, timezone

    assert applications.try_acquire_generation_lock("Dice", "1") is True
    apps = applications.load_applications()
    recent_time = datetime.now(timezone.utc) - timedelta(minutes=1)
    for app in apps:
        if app["source"] == "Dice" and app["job_id"] == "1":
            app["generation_lock_acquired_at"] = recent_time.isoformat()
    applications._save_all(apps)

    assert applications.try_acquire_generation_lock("Dice", "1") is False


# --- set_discussion_status() (2026-08-13, "Discuss & draft" basket op) ---


def test_set_discussion_status_creates_record_when_none_exists(isolated_data):
    applications.set_discussion_status("Dice", "1", "awaiting_discussion", board_message_id="abc-123")
    app = applications.get_application("Dice", "1")
    assert app is not None
    assert app["status"] == "under review"  # get-or-created default
    assert app["discussion_status"] == "awaiting_discussion"
    assert app["discussion_board_message_id"] == "abc-123"


def test_set_discussion_status_rejects_unknown_status(isolated_data):
    import pytest
    with pytest.raises(ValueError):
        applications.set_discussion_status("Dice", "1", "not_a_real_status")


def test_set_discussion_status_none_clears_status_and_board_message_id(isolated_data):
    applications.set_discussion_status("Dice", "1", "awaiting_discussion", board_message_id="abc-123")
    applications.set_discussion_status("Dice", "1", None)
    app = applications.get_application("Dice", "1")
    assert app["discussion_status"] is None
    assert app["discussion_board_message_id"] is None


def test_set_discussion_status_keeps_board_message_id_across_transitions(isolated_data):
    applications.set_discussion_status("Dice", "1", "awaiting_discussion", board_message_id="abc-123")
    applications.set_discussion_status("Dice", "1", "drafting_final")
    app = applications.get_application("Dice", "1")
    # board_message_id wasn't re-passed on this call - must not be wiped.
    assert app["discussion_board_message_id"] == "abc-123"
    assert app["discussion_status"] == "drafting_final"


def test_set_discussion_status_failed_stores_error(isolated_data):
    applications.set_discussion_status("Dice", "1", "failed", error="cover_letter: web search failed")
    app = applications.get_application("Dice", "1")
    assert app["discussion_status"] == "failed"
    assert app["discussion_error"] == "cover_letter: web search failed"


def test_set_discussion_status_does_not_disturb_existing_application_fields(isolated_data):
    applications.upsert_application("Dice", "1", status="applied", resume_text="draft v1")
    applications.set_discussion_status("Dice", "1", "awaiting_discussion")
    app = applications.get_application("Dice", "1")
    assert app["status"] == "applied"
    assert app["resume_text"] == "draft v1"
