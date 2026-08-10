"""Covers the 2026-08-08 score-first resume flow (docs/score-first-resume-
flow-spec.md, Option B layout, items 3/5/6 - the frontend half; items
1/2/4/7 are ATS Engine's backend work, landed and merged 2026-08-08).
Replaces the old "answer a question, regenerate the whole resume every
single time" pattern (render_gap_questions_section) with
render_analyze_fit_section(): answers now auto-save on every rerun with no
button required (item 4), and generating is a separate, explicit action
(item 6's confirmation logic) rather than bundled into the same click as
saving.

tailoring.drafting.analyze_fit_before_drafting() and
check_regenerate_impact() are the real backend functions as of this file's
2026-08-08 rewrite (previously tailoring.score_first_flow_stub's temporary
stand-ins, deleted once the real functions landed - see
analyze_fit_before_drafting's own docstring for the swap history). These
tests exercise the real composed pipeline (baseline selection -> real
either/or-aware keyword scoring -> missing-required-keyword-to-question
folding), not a stub's approximation - fixture job records carry real
ats_required_keywords so the projected score/point values are the actual
deterministic scorer's output, not a hand-guessed number. Tests that need
to force a specific check_regenerate_impact branch (has_new_info True/False)
monkeypatch it on tailoring.drafting directly (not the name app.py imports
it as - AppTest re-executes app.py fresh every .run(), so patching a name
aliased inside app.py itself never takes effect)."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score, load_jobs
from tailoring.applications import upsert_application, get_application
from tailoring.ats_score import score_resume_against_keywords
from tailoring.drafting import save_gap_answers, gap_scan_baseline_fingerprint

APP_PATH = "src/ui/app.py"

REQUIRED_KEYWORDS = ["Python", "Databricks"]
INITIAL_RESUME_TEXT = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nPython"
REGENERATED_RESUME_TEXT = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nPython, Databricks"

# Real output of the actual deterministic scorer for the fixture data above
# (1/2 required keywords matched - "Databricks" missing) - computed once
# here and reused, rather than a hand-guessed number that could silently
# drift from what score_resume_against_keywords actually does.
_INITIAL_SCORE_RESULT = score_resume_against_keywords(REQUIRED_KEYWORDS, [], INITIAL_RESUME_TEXT)
INITIAL_SCORE = _INITIAL_SCORE_RESULT["ats_score"]
DATABRICKS_POINT_VALUE = _INITIAL_SCORE_RESULT["missing_required_keywords"][0]["point_value"]
DATABRICKS_QUESTION_TEXT = (
    "The posting requires \"Databricks\" - do you have real, genuine experience with it? "
    "If so, briefly describe it so it can be added to your resume."
)


@pytest.fixture
def results_app_with_gap_questions(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")

    job = {
        "source": "Dice", "job_id": "job1", "title": "Director, Gaps", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python, Databricks.",
        "ats_required_keywords": REQUIRED_KEYWORDS, "ats_preferred_keywords": [],
    }
    save_jobs([job])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text=INITIAL_RESUME_TEXT,
        resume_ats_score=INITIAL_SCORE, resume_ats_rationale=_INITIAL_SCORE_RESULT["ats_rationale"],
        resume_ats_next_actions=[],
        # Matches what _merge_keyword_gap_questions would have already
        # produced and stored at the time this resume was drafted (real
        # production shape - see tailoring.drafting._draft_one/
        # _merge_keyword_gap_questions) - the point_value is already
        # baked in from that original merge, not something Step 1
        # re-derives independently.
        resume_clarifying_questions=[{
            "type": "skill_gap", "skill": "Databricks", "question": DATABRICKS_QUESTION_TEXT,
            "suggested_answer": "Unknown - please describe your real experience (if any) with this.",
            "point_value": DATABRICKS_POINT_VALUE,
        }],
        # Marks the free-form gap scan as already up to date for THIS
        # resume version (2026-08-09's auto-fire feature - see
        # app.py's _analyze_fit_with_auto_gap_scan) - every test in this
        # file predates and is unrelated to that feature; without this,
        # the very first render would auto-fire request_additional_gap_
        # questions() for real (raising DraftingNotConfigured in this
        # test env, silently swallowed) or - worse, for the tests that DO
        # monkeypatch it - fire it a SECOND, unintended time on top of
        # whatever the test itself triggers, double-appending a question
        # a test double doesn't dedupe the way the real function does.
        # Dedicated tests for the auto-fire behavior itself deliberately
        # build their OWN fixture without this, further down.
        resume_gap_scan_fingerprint=gap_scan_baseline_fingerprint(job, {"resume_text": INITIAL_RESUME_TEXT}),
    )
    return AppTest.from_file(APP_PATH)


def _fake_generate_documents(job, profile, doc_keys, on_progress=None):
    return {"resume": {
        "text": REGENERATED_RESUME_TEXT,
        "suggested_strategy_tag": "", "ats_score": 95,
        "ats_rationale": "Matched 2/2 keywords.", "ats_next_actions": [], "clarifying_questions": [],
    }}


def _answer_databricks_question_directly(answer="Yes, led a 2-year Databricks migration."):
    # Calls the real save path directly instead of driving it through
    # box.set_value(at).run() - AppTest limitation found while building
    # the toast+rerun fix (2026-08-09): once save_gap_answers()'s own new
    # st.rerun() makes this exact text_area disappear from open_questions
    # (correctly - it's genuinely answered now, confirmed independently
    # against the real function's output), AppTest's internal widget-tree
    # bookkeeping for that SPECIFIC widget breaks on any FURTHER .run()/
    # .click().run() in the SAME test - a KeyError trying to read a
    # session_state entry Streamlit itself already garbage-collected once
    # the widget stopped rendering. Reproduced independently of this
    # fix's own logic (confirmed correct via a standalone script: real
    # open_questions correctly excludes the answered skill) - specific to
    # widgets that vanish as a direct result of their OWN .set_value()
    # interaction being processed, not to reruns/disappearances
    # triggered any other way. Calling the underlying function directly
    # (matching exactly what app.py's own save_gap_answers([...]) call
    # does) produces the identical real state without ever exercising
    # AppTest's own set_value() path, sidestepping the limitation
    # entirely while still testing the real downstream behavior.
    job = next(j for j in load_jobs() if j["source"] == "Dice" and j["job_id"] == "job1")
    save_gap_answers(job, [{
        "skill": "Databricks", "type": "skill_gap", "answer": answer,
        "question": DATABRICKS_QUESTION_TEXT,
    }])


def test_gap_question_renders_inline_on_results_tab(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    assert any(t.label == DATABRICKS_QUESTION_TEXT for t in at.text_area)
    assert any(b.key and b.key.startswith("analyzefit_generate_") for b in at.button)
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "see the" not in markdown_text.lower() or "profile gaps" not in markdown_text.lower()


def test_gap_question_suggested_answer_prefilled_inline(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    assert box.value == "Unknown - please describe your real experience (if any) with this."


def test_skill_gap_question_shows_a_point_badge(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    badge_lines = [m.value for m in at.markdown if "-badge[" in m.value]
    assert any("pts" in b for b in badge_lines)
    assert any(f"{DATABRICKS_POINT_VALUE:g}" in b for b in badge_lines)


def test_free_form_question_with_no_matching_keyword_shows_value_not_yet_known(results_app_with_gap_questions):
    # Real gap General caught Zahir hit live 2026-08-09: an AI-generated
    # free-form fact-finding question (team size, budget - no
    # corresponding extracted keyword) used to render no badge at all,
    # which read as broken rather than "this one genuinely has no
    # deterministic value to show."
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    upsert_application(
        "Dice", "job1", status="under review",
        resume_clarifying_questions=[{
            "type": "skill_gap", "skill": "SK Life Science IT team size",
            "question": "How many engineers are on the SK Life Science IT team?",
            "suggested_answer": "",
        }],
    )
    at.run(timeout=30)

    assert not at.exception
    badge_lines = [m.value for m in at.markdown if "-badge[" in m.value]
    assert any("value not yet known" in b for b in badge_lines)


def test_projected_score_moves_after_answering_before_generating(results_app_with_gap_questions):
    # Real bug Zahir hit live 2026-08-09: he edited a real answer, waited,
    # and the "Projected score" metric correctly stayed flat - because it
    # was re-scoring the SAME unchanged resume_text, not actually
    # projecting anything. It must now genuinely move once an answer is
    # confirmed, without ever clicking Generate.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    metric = next(m for m in at.metric if m.label == "Projected score")
    assert metric.value == f"{INITIAL_SCORE}/100"

    _answer_databricks_question_directly()
    at.run(timeout=30)

    assert not at.exception
    metric = next(m for m in at.metric if m.label == "Projected score")
    projected = int(metric.value.split("/")[0])
    assert projected > INITIAL_SCORE
    # Still hasn't actually regenerated anything.
    assert get_application("Dice", "job1")["resume_ats_score"] == INITIAL_SCORE


def test_disqualifier_question_gets_no_point_badge_and_distinct_flag_note(results_app_with_gap_questions):
    # A disqualifier_check question comes from the AI's own past drafted
    # resume_clarifying_questions (missing_required_keywords never
    # produces one - that mechanism only knows about literal keyword
    # gaps, not standing preferences), so this seeds it there directly.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    upsert_application(
        "Dice", "job1", status="under review",
        resume_clarifying_questions=[{
            "type": "disqualifier_check", "skill": "role_level",
            "question": "Should VP/CIO roles below a certain org size be excluded going forward?",
            "suggested_answer": "",
        }],
    )
    at.run(timeout=30)

    assert not at.exception
    badge_lines = [m.value for m in at.markdown if "-badge[" in m.value]
    assert any("standing pref" in b for b in badge_lines)
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "applies to every" in markdown_text.lower()


def test_answer_saves_immediately_without_clicking_any_button(results_app_with_gap_questions):
    # Item 4: answers persist even if the user never reaches Generate -
    # real regression test for the old design's coupling of "answer
    # saved" to "document generated" (spec's explicit warning about this).
    # Deliberately drives the real text_area (not
    # _answer_databricks_question_directly()) - this is specifically
    # testing that the WIDGET ITSELF triggers the auto-save with no
    # button click, not just the downstream save behavior.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, led a 2-year Databricks migration.")
    at.run(timeout=30)  # a rerun from the text_area itself, not any button

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile().get("gap_interview_answers", [])
    assert any(a["skill"] == "Databricks" and "migration" in a["answer"] for a in answers)
    # Nothing was regenerated just from answering.
    assert get_application("Dice", "job1")["resume_ats_score"] == INITIAL_SCORE


def test_answering_shows_a_saved_toast(results_app_with_gap_questions):
    # Real gap Zahir hit live (2026-08-09): the save genuinely worked
    # (verified directly against his real profile data) but the screen
    # gave zero visible confirmation either way - no toast, no checkmark,
    # nothing changed, no way to tell "saved silently" from "ignored"
    # without checking the raw data. st.toast (not st.success/st.info,
    # which wouldn't survive the immediate st.rerun() right after it) is
    # what the real fix uses - reliably observable via AppTest through a
    # real widget interaction, unlike the "does the list already reflect
    # the drop on this same cycle" half of this fix (see
    # test_open_questions_correctly_excludes_an_answered_skill below for
    # why that one isn't AppTest-testable through the widget itself, and
    # this worktree's live-verification notes for the real-browser check).
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, led a 2-year Databricks migration.")
    at.run(timeout=30)

    assert not at.exception
    assert any(t.value == "Saved." for t in at.toast)


def test_open_questions_correctly_excludes_an_answered_skill(results_app_with_gap_questions):
    # The other half of the same fix (see test_answering_shows_a_saved_
    # toast above): open_questions is computed once at the top of
    # render_analyze_fit_section, before the per-question loop reaches
    # the save - without a rerun right after saving, the just-answered
    # question would still show as open for one more render. AppTest
    # limitation found building this fix: driving this through the real
    # text_area widget (box.set_value().run(), even with one extra plain
    # .run() to let the triggered rerun settle) hits a real AppTest bug -
    # once a keyed widget disappears as a direct result of its OWN
    # .set_value() being processed, AppTest's internal widget-tree
    # bookkeeping for that specific widget breaks on any further .run(),
    # raising a KeyError trying to read a session_state entry Streamlit
    # itself already garbage-collected once the widget stopped
    # rendering - reproducible independently of whether the underlying
    # fix is correct. So this asserts the real function's output directly
    # (same one render_analyze_fit_section calls) - the actual
    # same-cycle "does it disappear from what's rendered" claim is
    # verified live in a real browser instead (this worktree's
    # live-verification notes), matching the documented-limitation
    # pattern already used elsewhere in this suite (canvas ButtonColumns,
    # etc.) rather than a test that can't reliably pass or fail on its
    # own merits.
    from tailoring.applications import get_application
    from tailoring.drafting import analyze_fit_before_drafting
    from profile.storage import load_profile

    job = next(j for j in load_jobs() if j["source"] == "Dice" and j["job_id"] == "job1")
    app_record = get_application("Dice", "job1")
    before = analyze_fit_before_drafting(job, load_profile(), app_record)
    assert any(q["skill"] == "Databricks" for q in before["open_questions"])

    _answer_databricks_question_directly()

    after = analyze_fit_before_drafting(job, load_profile(), app_record)
    assert not any(q["skill"] == "Databricks" for q in after["open_questions"])


def test_untouched_suggested_answer_does_not_get_saved_as_a_real_confirmed_answer(results_app_with_gap_questions):
    # Real bug found live-verifying this stub-swap against real production
    # data (2026-08-08): the suggested_answer prefill ("Unknown - please
    # describe your real experience...") is itself non-empty text, so a
    # bare "is there text in the box" check silently saved that untouched
    # HEDGED GUESS as a real confirmed answer the moment the page
    # rendered - before the user ever looked at it. Reproduced live:
    # simply opening Profile Gaps once silently wrote 31 placeholder
    # answers into the real profile as if they were confirmed facts.
    # Merely rendering (even across several reruns) must never count as
    # answering - only a genuine edit away from the exact prefill does.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)
    at.run(timeout=30)  # a second, unrelated rerun - still untouched
    at.run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile().get("gap_interview_answers", [])
    assert not any(a["skill"] == "Databricks" for a in answers)


def test_generate_button_regenerates_using_the_confirmed_answer(results_app_with_gap_questions, monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)
    # No prior draft's own resume_clarifying_questions and a fresh
    # gap_interview_answers entry means check_regenerate_impact sees real
    # new info (date_captured >= documents_drafted_at, which is unset) -
    # the non-blocking heads-up path, so Generate proceeds immediately.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    _answer_databricks_question_directly()
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert app_record["resume_ats_score"] == 95


def _fake_generate_documents_that_drops_python(job, profile, doc_keys, on_progress=None):
    # Simulates the real Upstream Bio regression (2026-08-09): the
    # regenerate fixes the ORIGINAL gap (Databricks) but silently drops a
    # keyword the previous draft had matched (Python) - net score can look
    # fine (still 1/2 matched) while a real, specific regression happened.
    return {"resume": {
        "text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nDatabricks",
        "suggested_strategy_tag": "", "ats_score": 76,
        "ats_rationale": "Matched 1/2 keywords.", "ats_next_actions": [], "clarifying_questions": [],
    }}


def test_generate_warns_when_a_regenerate_drops_a_previously_matched_keyword(results_app_with_gap_questions, monkeypatch):
    # Real bug caught live 2026-08-09 (General, watching Zahir's session):
    # a full-rewrite regenerate traded one matched keyword for another
    # with a flat net score, completely invisible unless the old and new
    # keyword-match sets are actually diffed - CLAUDE.md known failure
    # pattern #2, reproduced on a real job (Upstream Bio: "Engineering"
    # fixed, "life sciences" silently dropped).
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents_that_drops_python)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    _answer_databricks_question_directly()
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    # st.toast, not st.warning - a warning shown here would flash and
    # vanish (both callers of regenerate_resume_and_persist st.rerun()
    # unconditionally right after this success path), so the real
    # implementation uses st.toast(), which survives a rerun.
    toasts = [t.value for t in at.toast]
    assert any("Python" in t for t in toasts)
    assert any("no longer covers" in t for t in toasts)


def test_generate_shows_no_regression_warning_when_nothing_was_lost(results_app_with_gap_questions, monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    _answer_databricks_question_directly()
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    assert not at.warning


def test_answer_more_questions_shows_a_genuinely_new_question(results_app_with_gap_questions, monkeypatch):
    # Score-first-resume-flow spec item 5: clicking "Answer more questions"
    # must trigger a real new round of AI generation, not just re-show
    # whatever was already there - real bug Zahir hit live 2026-08-09
    # (the button used to be a bare st.rerun() with no generation call at
    # all behind it, so it could never surface anything new or say so).
    import tailoring.drafting as drafting

    new_question = {
        "type": "skill_gap", "skill": "Team size at Acme",
        "question": "How big was the team?", "suggested_answer": "",
    }
    monkeypatch.setattr(
        drafting, "request_additional_gap_questions",
        lambda job, profile, app_record, model=None, on_progress=None: {
            "added_count": 1, "new_questions": [new_question],
            "merged_clarifying_questions": (app_record.get("resume_clarifying_questions") or []) + [new_question],
        },
    )

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    button = next(b for b in at.button if b.key == f"answermore_Dice_job1")
    button.click().run(timeout=30)

    assert not at.exception
    assert any("How big was the team?" in t.label for t in at.text_area)
    app_record = get_application("Dice", "job1")
    assert any(q.get("skill") == "Team size at Acme" for q in app_record["resume_clarifying_questions"])


def test_answer_more_questions_shows_honest_message_when_ai_finds_nothing_new(results_app_with_gap_questions, monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(
        drafting, "request_additional_gap_questions",
        lambda job, profile, app_record, model=None, on_progress=None: {
            "added_count": 0, "new_questions": [],
            "merged_clarifying_questions": app_record.get("resume_clarifying_questions") or [],
        },
    )

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    button = next(b for b in at.button if b.key == f"answermore_Dice_job1")
    button.click().run(timeout=30)

    assert not at.exception
    # The Databricks question from before is still there (nothing new
    # replaced it) - just a plain toast, no fabricated content.
    assert any("Databricks" in t.label for t in at.text_area)


def test_resume_expander_auto_expands_right_after_answering_a_gap_question(results_app_with_gap_questions, monkeypatch):
    # Zahir's explicit ask 2026-08-06: the new score/rationale/questions
    # must be part of what he sees immediately after generating - not
    # behind a collapsed expander he has to remember to reopen.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    _answer_databricks_question_directly()
    at.run(timeout=30)
    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    resume_expander = next(e for e in at.expander if e.label == "Resume (drafted)")
    assert resume_expander.proto.expanded


def test_score_delta_shown_after_regenerating(results_app_with_gap_questions, monkeypatch):
    # Zahir's explicit ask 2026-08-06: changing an answer must visibly show
    # the old -> new score change, reusing the existing delta-arrow pattern
    # already built for the JD-update flow, not just a bare new number.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    _answer_databricks_question_directly()
    at.run(timeout=30)
    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    metric = next(m for m in at.metric if m.label == "ATS compatibility score")
    assert metric.value == "95/100"
    assert metric.delta is not None
    assert str(95 - INITIAL_SCORE) in metric.delta


def test_generate_with_no_new_info_opens_a_blocking_confirmation_dialog(results_app_with_gap_questions, monkeypatch):
    # Item 6: regenerating with nothing new confirmed is pure downside
    # risk (a full rewrite that could accidentally drop a matched
    # keyword) - forces the has_new_info=False branch (no fresh
    # gap_interview_answers) to verify the blocking gate.
    import tailoring.drafting as drafting

    monkeypatch.setattr(
        drafting, "check_regenerate_impact",
        lambda job, app_record, profile: {
            "has_new_info": False, "new_fact_count": None, "estimated_new_score": None,
            "cost_estimate": None, "last_generation_cost": 0.0421, "current_score": INITIAL_SCORE,
        },
    )
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    # No regeneration happened yet - blocked behind the dialog.
    assert get_application("Dice", "job1")["resume_ats_score"] == INITIAL_SCORE
    assert "regen_confirm_pending" in at.session_state
    dialog_text = " ".join(m.value for m in at.markdown)
    assert "0.04" in dialog_text
    assert any(b.key == "regen_confirm_confirm" for b in at.button)
    assert any(b.key == "regen_confirm_cancel" for b in at.button)


def test_confirming_the_blocking_dialog_regenerates(results_app_with_gap_questions, monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)
    monkeypatch.setattr(
        drafting, "check_regenerate_impact",
        lambda job, app_record, profile: {
            "has_new_info": False, "new_fact_count": None, "estimated_new_score": None,
            "cost_estimate": None, "last_generation_cost": 0.0421, "current_score": INITIAL_SCORE,
        },
    )
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    confirm_button = next(b for b in at.button if b.key == "regen_confirm_confirm")
    confirm_button.click().run(timeout=30)

    assert not at.exception
    assert get_application("Dice", "job1")["resume_ats_score"] == 95


def test_cancelling_the_blocking_dialog_does_not_regenerate(results_app_with_gap_questions, monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(
        drafting, "check_regenerate_impact",
        lambda job, app_record, profile: {
            "has_new_info": False, "new_fact_count": None, "estimated_new_score": None,
            "cost_estimate": None, "last_generation_cost": 0.0421, "current_score": INITIAL_SCORE,
        },
    )
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    cancel_button = next(b for b in at.button if b.key == "regen_confirm_cancel")
    cancel_button.click().run(timeout=30)

    assert not at.exception
    assert get_application("Dice", "job1")["resume_ats_score"] == INITIAL_SCORE
    assert "regen_confirm_pending" not in at.session_state


def test_no_open_questions_shows_the_exhausted_message(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    # A fully-matching resume - no missing required keywords left to ask
    # about, and no stale stored questions carried over from before (a
    # real regenerate would have replaced resume_clarifying_questions
    # with a fresh, empty merge result at this same point).
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text=REGENERATED_RESUME_TEXT, resume_clarifying_questions=[],
    )
    at.run(timeout=30)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "no more real gaps found" in markdown_text.lower()


def test_gap_question_also_still_appears_on_profile_gaps_tab(results_app_with_gap_questions):
    # Additive, not a replacement - Zahir's explicit requirement, same
    # principle as the JD-box design: Profile Gaps still consolidates
    # across every job for scanning; Results tab is for a job already in
    # focus. Both must work.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    assert not at.exception
    assert any("Databricks" in t.label for t in at.text_area)


def test_profile_gaps_expander_count_reflects_missing_keyword_questions_too(results_app_with_gap_questions):
    # Real bug found live-verifying this stub-swap against real data
    # (2026-08-08): the expander's "(N open)" label counted only
    # len(resume_clarifying_questions) - the stored, AI-drafted-at-the-
    # time set - which undercounts once a required keyword is missing
    # that ISN'T yet captured in that stored list (e.g. keyword
    # extraction ran/changed after the last draft). The real open count
    # is len(open_questions) from analyze_fit_before_drafting, which also
    # folds in fresh missing_required_keywords - the label must match
    # what's actually inside the expander when opened, not undercount it.
    save_jobs([{
        "source": "Dice", "job_id": "job2", "title": "Director, Two Gaps", "organization": "Beta Inc",
        "location": "Remote", "description": "Requirements: Python, Databricks, Snowflake.",
        "ats_required_keywords": ["Python", "Databricks", "Snowflake"], "ats_preferred_keywords": [],
    }])
    update_job_score("Dice", "job2", 80, "Good match.")
    upsert_application(
        "Dice", "job2", status="under review",
        resume_text=INITIAL_RESUME_TEXT,  # has Python, missing both Databricks and Snowflake
        resume_ats_score=50, resume_ats_rationale="placeholder", resume_ats_next_actions=[],
        # Only ONE of the two real gaps is captured in the stored field -
        # the stale-count bug this test guards against.
        resume_clarifying_questions=[{
            "type": "skill_gap", "skill": "Databricks", "question": DATABRICKS_QUESTION_TEXT,
            "suggested_answer": "Unknown - please describe your real experience (if any) with this.",
            "point_value": DATABRICKS_POINT_VALUE,
        }],
    )

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    assert not at.exception
    expander = next(e for e in at.expander if "Director, Two Gaps" in e.label)
    assert "(2 open)" in expander.label, expander.label


# --- Auto-fire free-form gap scan (2026-08-09) ---
# Zahir's explicit design, confirmed with General: pre-generate Analyze Fit
# showed "No more real gaps found" on a real job, he generated once, and
# the POST-generate panel then surfaced 6 brand-new free-form questions -
# nothing had ever run the free-form scan (request_additional_gap_
# questions) before that first click. app.py's _analyze_fit_with_auto_gap_
# scan now fires it automatically, once per resume version, the first time
# Analyze Fit is opened for that version - see that function's own
# docstring for the full design (fingerprint-based "already scanned this
# version" check, deliberately excluded from the Profile Gaps bulk loop).

@pytest.fixture
def results_app_before_any_gap_scan(isolated_data, monkeypatch):
    # Deliberately does NOT set resume_gap_scan_fingerprint (unlike
    # results_app_with_gap_questions above) - these tests exist
    # specifically to exercise the auto-fire path that fixture is set up
    # to suppress.
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Gaps", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python, Databricks.",
        "ats_required_keywords": REQUIRED_KEYWORDS, "ats_preferred_keywords": [],
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text=INITIAL_RESUME_TEXT,
        resume_ats_score=INITIAL_SCORE, resume_ats_rationale=_INITIAL_SCORE_RESULT["ats_rationale"],
        resume_ats_next_actions=[],
        resume_clarifying_questions=[],
    )
    return AppTest.from_file(APP_PATH)


def test_analyze_fit_auto_fires_the_gap_scan_on_first_open(results_app_before_any_gap_scan, monkeypatch):
    import tailoring.drafting as drafting

    call_count = [0]
    new_question = {
        "type": "skill_gap", "skill": "Team size at Acme",
        "question": "How big was the team?", "suggested_answer": "",
    }

    def _fake(job, profile, app_record, model=None, on_progress=None):
        call_count[0] += 1
        return {
            "added_count": 1, "new_questions": [new_question],
            "merged_clarifying_questions": (app_record.get("resume_clarifying_questions") or []) + [new_question],
        }

    monkeypatch.setattr(drafting, "request_additional_gap_questions", _fake)

    at = results_app_before_any_gap_scan
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    assert call_count[0] == 1  # fired automatically, no button click needed
    assert any("How big was the team?" in t.label for t in at.text_area)
    app_record = get_application("Dice", "job1")
    assert any(q.get("skill") == "Team size at Acme" for q in app_record["resume_clarifying_questions"])
    assert app_record.get("resume_gap_scan_fingerprint")  # persisted so it won't re-fire


def test_analyze_fit_does_not_refire_the_gap_scan_for_the_same_resume_version(results_app_before_any_gap_scan, monkeypatch):
    import tailoring.drafting as drafting

    call_count = [0]

    def _fake(job, profile, app_record, model=None, on_progress=None):
        call_count[0] += 1
        return {"added_count": 0, "new_questions": [], "merged_clarifying_questions": app_record.get("resume_clarifying_questions") or []}

    monkeypatch.setattr(drafting, "request_additional_gap_questions", _fake)

    at = results_app_before_any_gap_scan
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)
    assert call_count[0] == 1

    # Re-open/re-render the SAME job with the SAME resume version - must
    # not fire again just because the panel was opened a second time.
    at.run(timeout=30)
    assert call_count[0] == 1


def test_analyze_fit_gap_scan_failure_is_swallowed_not_a_hard_error(results_app_before_any_gap_scan, monkeypatch):
    # Fires automatically, not from an explicit click - a hard st.error
    # banner on an action Zahir didn't initiate would be more disruptive
    # than useful. Must degrade gracefully (soft toast, page still
    # renders) rather than crashing Analyze Fit entirely.
    import tailoring.drafting as drafting
    from tailoring.drafting import DraftingNotConfigured

    def _fake(job, profile, app_record, model=None, on_progress=None):
        raise DraftingNotConfigured("no API key in this test environment")

    monkeypatch.setattr(drafting, "request_additional_gap_questions", _fake)

    at = results_app_before_any_gap_scan
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert not app_record.get("resume_gap_scan_fingerprint")  # never persisted - next render will retry


def test_analyze_fit_refires_the_gap_scan_after_a_new_resume_version(results_app_before_any_gap_scan, monkeypatch):
    import tailoring.drafting as drafting

    call_count = [0]

    def _fake(job, profile, app_record, model=None, on_progress=None):
        call_count[0] += 1
        return {"added_count": 0, "new_questions": [], "merged_clarifying_questions": app_record.get("resume_clarifying_questions") or []}

    monkeypatch.setattr(drafting, "request_additional_gap_questions", _fake)

    at = results_app_before_any_gap_scan
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)
    assert call_count[0] == 1

    # A fresh Generate produced a genuinely new resume version - the scan
    # must run again for it, not stay suppressed forever.
    upsert_application("Dice", "job1", status="under review", resume_text=REGENERATED_RESUME_TEXT)
    at.run(timeout=30)
    assert call_count[0] == 2


def test_profile_gaps_tab_does_not_auto_fire_the_gap_scan(results_app_before_any_gap_scan, monkeypatch):
    # Deliberate scope decision (2026-08-09, per the design report to
    # General): firing this for every application with open questions
    # simultaneously the first time this ships would be a real, sudden
    # cost/latency spike - the Profile Gaps tab's bulk cross-job loop
    # must keep calling analyze_fit_before_drafting() directly.
    import tailoring.drafting as drafting

    # Needs a REAL stored open question (not the fixture's empty list) so
    # get_applications_with_open_clarifying_questions() actually surfaces
    # this job into the Profile Gaps loop at all - otherwise the test
    # would trivially "pass" without the loop ever running for it.
    upsert_application(
        "Dice", "job1", status="under review",
        resume_clarifying_questions=[{
            "type": "skill_gap", "skill": "Databricks", "question": DATABRICKS_QUESTION_TEXT,
            "suggested_answer": "Unknown - please describe your real experience (if any) with this.",
            "point_value": DATABRICKS_POINT_VALUE,
        }],
    )

    call_count = [0]

    def _fake(job, profile, app_record, model=None, on_progress=None):
        call_count[0] += 1
        return {"added_count": 0, "new_questions": [], "merged_clarifying_questions": []}

    monkeypatch.setattr(drafting, "request_additional_gap_questions", _fake)

    at = results_app_before_any_gap_scan
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    assert not at.exception
    assert call_count[0] == 0
