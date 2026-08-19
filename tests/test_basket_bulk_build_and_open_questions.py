"""Covers two real bugs Zahir found live 2026-08-19 while testing the
basket, both in render_basket_bar() (src/ui/app.py):

1. "Generate final resume for all N ready job(s)" (the bulk final-build
   button) counted a job as ready whenever subscription_qa_round > 0, with
   no check for whether that job already had a completed final build for
   the currently-checked document types - re-clicking it re-ran (and
   re-charged) already-succeeded jobs alongside genuinely new ones.
   _job_already_built() is the real-build-state check this is built on.

   Original fix (superseded): exclude already-built jobs from the bulk
   button's target set and show a persistent "already built" status line,
   while the job stays in the basket. Zahir's direct follow-up
   clarification the same day changed the intended behavior: a fully-built
   job should be REMOVED from the basket automatically "as the basket
   operation is done," not merely grayed out/skipped while it lingers.
   This is now implemented three ways, all reusing the existing
   remove_from_basket() (same function the manual per-job "X" button
   calls):
   - A general per-render check (runs before Section 2 even starts
     rendering rows) removes any basket job that's already fully built for
     the CURRENTLY CHECKED document types - covers jobs that were already
     built before this render even started (e.g. the successes from an
     earlier partial-failure batch), not just ones that just finished.
   - The per-job single "Generate final resume" button's success path
     removes that one job immediately once it's fully built for the
     doc_keys just requested, without waiting for the next render.
   - Each successful job in the bulk "Generate final resume for N" batch's
     results loop gets the same immediate-removal check.
   "Fully built" is judged strictly against what's actually checked below
   - a job built for "resume" alone counts as done (and is removed) when
   only "resume" is checked, even though a cover letter was never
   requested; toggling more doc types on does not resurrect a removed job,
   the same way remove_from_basket() has never behaved differently for any
   other removal path. The old "already built" markdown status line is
   kept as a defensive/mid-render fallback (still useful in the brief
   window before a removal + rerun actually lands) but is no longer the
   primary signal.

2. The "Open questions across your basket" aggregate panel
   (all_open_questions) pulled in resume_clarifying_questions from every
   basket job regardless of subscription_qa_loop_state, so a job already
   shown as "Plateaued... nothing more can be done" via the per-job status
   line just above still surfaced its last round's leftover questions in
   the aggregate box - contradicting itself. loop_state is now read once
   per job (reused for both the per-job status text and this filter, not
   recomputed) and a plateaued job's questions are excluded from
   all_open_questions.

Same streamlit.testing.v1.AppTest convention as
tests/test_recurring_gap_draft_with_ai.py/test_accept_all_gap_questions.py
(this codebase's established way of testing app.py-adjacent UI logic) for
the UI-level assertions, plus direct unit tests of the new
_job_already_built() helper (a pure function, imported straight off the
`ui.app` module - no Streamlit context needed for those)."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs
from tailoring.applications import record_subscription_qa_round, upsert_application

APP_PATH = "src/ui/app.py"


def _job(job_id, in_basket=True):
    return {
        "source": "linkedin", "job_id": job_id, "title": f"Role {job_id}", "organization": f"Org {job_id}",
        "in_basket": in_basket,
        "ats_required_keywords": [], "ats_preferred_keywords": [],
    }


def _draft_one_round(source, job_id, ats_score=70, loop_state="in_progress", clarifying_questions=None):
    """Puts a job through one real subscription draft round: creates the
    application record (upsert_application, same as run_subscription_round()
    does for its first round) then bumps subscription_qa_round/loop_state
    via record_subscription_qa_round() - the same two real calls the app's
    own subscription QA path makes, not a shortcut that could drift from
    what production actually writes."""
    upsert_application(
        source, job_id, status="under review",
        resume_text="draft text", resume_ats_score=ats_score,
        resume_clarifying_questions=clarifying_questions or [],
    )
    record_subscription_qa_round(source, job_id, ats_score=ats_score, loop_state=loop_state)


@pytest.fixture
def basket_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    return AppTest.from_file(APP_PATH)


# --- Bug 1: _job_already_built() unit tests ---


def test_already_built_true_when_every_checked_doc_type_has_real_output():
    import ui.app as app_module

    app_record = {
        "resume_draft_source": "paid", "resume_text": "a resume",
        "cover_letter_text": "a cover letter",
    }
    assert app_module._job_already_built(app_record, ["resume", "cover_letter"]) is True


def test_already_built_false_when_missing_even_one_checked_doc_type():
    import ui.app as app_module

    app_record = {
        "resume_draft_source": "paid", "resume_text": "a resume",
        "cover_letter_text": "",  # never actually built
    }
    assert app_module._job_already_built(app_record, ["resume", "cover_letter"]) is False


def test_already_built_false_when_resume_only_drafted_via_subscription_not_paid():
    import ui.app as app_module

    # subscription_resume_qa.py stamps resume_draft_source="subscription" for
    # a free Draft & score round - that must never count as "built" (it's
    # not the paid final build this button is gated on).
    app_record = {"resume_draft_source": "subscription", "resume_text": "a resume"}
    assert app_module._job_already_built(app_record, ["resume"]) is False


def test_already_built_false_for_empty_doc_keys():
    import ui.app as app_module

    app_record = {"resume_draft_source": "paid", "resume_text": "a resume"}
    assert app_module._job_already_built(app_record, []) is False


def test_already_built_checks_apply_answers_as_a_non_empty_list():
    import ui.app as app_module

    assert app_module._job_already_built({"apply_answers": []}, ["apply_answers"]) is False
    assert app_module._job_already_built({"apply_answers": [{"label": "x", "value": "y"}]}, ["apply_answers"]) is True


def test_already_built_false_for_unknown_doc_key_conservatively():
    import ui.app as app_module

    # An unrecognized/future doc_key can't be verified, so it's treated as
    # NOT built - keeps the job in the bulk target set (today's behavior)
    # rather than falsely claiming something unverifiable is done.
    assert app_module._job_already_built({"resume_draft_source": "paid", "resume_text": "x"}, ["resume", "future_doc_type"]) is False


# --- Bug 1: basket auto-clean (already-built jobs get REMOVED, not just
# excluded/grayed-out) ---


def _in_basket(source, job_id):
    from search.job_store import load_jobs

    job = next(j for j in load_jobs() if j.get("source") == source and j.get("job_id") == job_id)
    return bool(job.get("in_basket"))


def test_job_already_built_before_render_is_auto_removed_from_basket_on_load(basket_app):
    # The "10 he just finished" scenario: a job that was already fully
    # built in an earlier session/click, sitting in the basket when the
    # page next loads - must be cleaned up on THIS render, not only after
    # some new build action.
    save_jobs([_job("built"), _job("new")], apply_exclusion=False, review_required=False)
    _draft_one_round("linkedin", "built", ats_score=90, loop_state="ready")
    _draft_one_round("linkedin", "new", ats_score=70, loop_state="in_progress")
    upsert_application(
        "linkedin", "built", status="under review",
        resume_text="final resume text", resume_draft_source="paid",
        cover_letter_text="final cover letter",
    )

    at = basket_app
    at.run(timeout=30)

    assert not at.exception
    # "built" is fully built for the default-checked doc types (resume +
    # cover_letter) - removed automatically.
    assert _in_basket("linkedin", "built") is False
    # "new" only has a subscription round, no final build - stays.
    assert _in_basket("linkedin", "new") is True
    # NOTE: the removal toast is fired then immediately followed by
    # st.rerun() in the same script run - AppTest's `at.toast` reflects
    # only the FINAL completed run's own toasts, so a toast posted right
    # before a rerun it triggers itself isn't reliably observable through
    # this harness (not asserted here for that reason). The real,
    # reliably-testable assertion is the actual basket-membership change
    # above, not this cosmetic message.
    bulk_button = next(b for b in at.button if b.key == "basket_finalbuild_bulk")
    assert "1 new/not-yet-built job(s)" in bulk_button.label


def test_job_missing_one_checked_doc_type_stays_in_basket_and_is_ready(basket_app, monkeypatch):
    # is_configured() gates every generate button in app.py separately from
    # the ready/already-built logic under test - the autouse
    # _no_real_anthropic_api_calls fixture unsets ANTHROPIC_API_KEY for
    # every test, so without this the button would be disabled for an
    # unrelated reason and this test would pass for the wrong reason.
    import tailoring.drafting as drafting
    monkeypatch.setattr(drafting, "is_configured", lambda: True)

    save_jobs([_job("partial")], apply_exclusion=False, review_required=False)
    _draft_one_round("linkedin", "partial", ats_score=90, loop_state="ready")
    # Resume built, but cover_letter never was - both are checked by
    # default, so this job is NOT fully built and must stay in the basket.
    upsert_application(
        "linkedin", "partial", status="under review",
        resume_text="final resume text", resume_draft_source="paid",
    )

    at = basket_app
    at.run(timeout=30)

    assert not at.exception
    assert _in_basket("linkedin", "partial") is True
    bulk_button = next(b for b in at.button if b.key == "basket_finalbuild_bulk")
    assert "1 new/not-yet-built job(s)" in bulk_button.label
    assert not bulk_button.disabled
    # The per-job button also stays live for it, regardless of the partial
    # build already present - an explicit single-job rebuild must still be
    # possible.
    per_job_button = next(b for b in at.button if b.key == "basket_item_finalbuild_linkedin_partial")
    assert not per_job_button.disabled


def test_completing_the_per_job_build_removes_it_from_the_basket(basket_app, monkeypatch):
    import tailoring.drafting as drafting
    import tailoring.bulk_generate as bulk_generate

    monkeypatch.setattr(drafting, "is_configured", lambda: True)

    save_jobs([_job("job1")], apply_exclusion=False, review_required=False)
    _draft_one_round("linkedin", "job1", ats_score=70, loop_state="in_progress")

    # generate_for_job() is the real paid call - mocked at its DEFINITION
    # module (tailoring.bulk_generate), not ui.app's already-bound import,
    # per this suite's established AppTest convention (see
    # tests/test_recurring_gap_draft_with_ai.py's module docstring: AppTest
    # re-executes app.py's own `from tailoring.bulk_generate import ...`
    # line on every .run(), so patching ui.app's bound name never actually
    # intercepts anything). This fake also performs the real persistence
    # side effect (upsert_application) so _job_already_built() sees
    # genuinely-updated data afterward, same as the real function would
    # leave behind.
    def _fake_generate_for_job(job, profile, doc_keys, on_progress=None):
        upsert_application(
            job["source"], job["job_id"], status="under review",
            resume_text="final resume text", resume_draft_source="paid" if "resume" in doc_keys else None,
            cover_letter_text="final cover letter" if "cover_letter" in doc_keys else None,
        )
        return {"ok": True, "locked": False, "errors": {}}
    monkeypatch.setattr(bulk_generate, "generate_for_job", _fake_generate_for_job)

    at = basket_app
    at.run(timeout=30)
    assert _in_basket("linkedin", "job1") is True  # only drafted so far, not built - still present

    per_job_button = next(b for b in at.button if b.key == "basket_item_finalbuild_linkedin_job1")
    per_job_button.click().run(timeout=30)

    assert not at.exception
    # Both default-checked doc types (resume + cover_letter) are now built
    # -> removed immediately, not on some later render.
    assert _in_basket("linkedin", "job1") is False


def test_completing_the_bulk_build_removes_succeeded_jobs_from_the_basket(basket_app, monkeypatch):
    import tailoring.drafting as drafting
    import tailoring.bulk_generate as bulk_generate

    monkeypatch.setattr(drafting, "is_configured", lambda: True)

    save_jobs([_job("job1"), _job("job2")], apply_exclusion=False, review_required=False)
    _draft_one_round("linkedin", "job1", ats_score=70, loop_state="in_progress")
    _draft_one_round("linkedin", "job2", ats_score=70, loop_state="in_progress")

    def _fake_generate_for_basket(jobs, profile, doc_keys, on_progress=None, max_workers=None):
        results = {}
        for job in jobs:
            source, job_id = job["source"], job["job_id"]
            if job_id == "job1":
                upsert_application(
                    source, job_id, status="under review",
                    resume_text="final resume text", resume_draft_source="paid",
                    cover_letter_text="final cover letter",
                )
                results[(source, job_id)] = {"ok": True, "locked": False, "errors": {}}
            else:
                # job2 fails - must stay in the basket, not be removed.
                results[(source, job_id)] = {"ok": False, "locked": False, "errors": {"resume": "boom"}}
        return results
    monkeypatch.setattr(bulk_generate, "generate_for_basket", _fake_generate_for_basket)

    at = basket_app
    at.run(timeout=30)
    bulk_button = next(b for b in at.button if b.key == "basket_finalbuild_bulk")
    bulk_button.click().run(timeout=30)

    assert not at.exception
    assert _in_basket("linkedin", "job1") is False  # succeeded and fully built -> removed
    assert _in_basket("linkedin", "job2") is True  # failed -> stays


# --- Bug 2: plateaued jobs excluded from the aggregate open-questions panel ---


def _question(text, skill="SAP S/4HANA"):
    return {"question": text, "type": "keyword_gap", "skill": skill, "suggested_answer": ""}


def test_plateaued_jobs_questions_excluded_from_aggregate_panel(basket_app):
    save_jobs([_job("plateaued"), _job("active")], apply_exclusion=False, review_required=False)
    _draft_one_round(
        "linkedin", "plateaued", ats_score=75, loop_state="plateaued",
        clarifying_questions=[_question("Plateaued job's leftover question?")],
    )
    _draft_one_round(
        "linkedin", "active", ats_score=70, loop_state="in_progress",
        clarifying_questions=[_question("Active job's real open question?")],
    )

    at = basket_app
    at.run(timeout=30)

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Active job's real open question?" in all_text
    assert "Plateaued job's leftover question?" not in all_text
    # The per-job status for the plateaued job still says so, right above.
    assert any("Plateaued at ATS" in m.value for m in at.markdown)


def test_non_plateaued_jobs_questions_still_appear(basket_app):
    save_jobs([_job("active")], apply_exclusion=False, review_required=False)
    _draft_one_round(
        "linkedin", "active", ats_score=70, loop_state="in_progress",
        clarifying_questions=[_question("Still-open question?")],
    )

    at = basket_app
    at.run(timeout=30)

    assert not at.exception
    assert any("Still-open question?" in m.value for m in at.markdown)


def test_zero_round_job_never_drafted_appears_normally_and_has_no_questions(basket_app):
    save_jobs([_job("fresh")], apply_exclusion=False, review_required=False)

    at = basket_app
    at.run(timeout=30)

    assert not at.exception
    assert any("Not drafted yet" in m.value for m in at.markdown)
