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
