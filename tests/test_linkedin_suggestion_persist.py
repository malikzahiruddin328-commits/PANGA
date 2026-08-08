"""Real bug (Mirror's proactive sweep, 2026-08-08, confirmed against real
stored data before it actually fired for Zahir - the one real analysis on
disk still had all 5 suggestions at default "suggested"): save_analysis()
did a full-replace of the suggestions list on every "Analyze profile" run,
with no merge/carry-forward against mark_suggestion_status()'s
applied/dismissed state. Re-running analysis (e.g. after uploading a
refreshed LinkedIn profile PDF) silently reset every suggestion back to
"suggested", wiping out anything Zahir had already marked applied or
dismissed - same failure shape as the resume-regenerate bug (CLAUDE.md
"Known failure patterns" #2), smaller blast radius since it's advisory
suggestion text rather than a drafted document.

Fixed by matching suggestion identity (section + current_text, or
section + suggested_text when there's no current_text to point at - see
_suggestion_identity's docstring) across a re-analysis and carrying
forward applied/dismissed status instead of resetting everything back to
"suggested"."""

from linkedin.storage import (
    get_active_suggestions,
    load_linkedin_profile,
    mark_suggestion_status,
    save_analysis,
)


def _suggestion(section, current_text, suggested_text, rationale="because"):
    return {"section": section, "current_text": current_text, "suggested_text": suggested_text, "rationale": rationale}


def test_applied_status_survives_a_reanalysis_of_the_same_content(isolated_data):
    save_analysis(
        [_suggestion("headline", "VP of IT", "VP of Information Technology & Digital Strategy")],
        profile_strength_score=60, profile_strength_rationale="early days", analyzed_at="2026-08-01",
    )
    first_id = load_linkedin_profile()["suggestions"][0]["id"]
    mark_suggestion_status(first_id, "applied")

    # Re-analysis proposes different WORDING for the suggestion (real LLM
    # non-determinism) but the underlying profile text it's about
    # (current_text) is unchanged - that's the part that should carry the
    # status forward, not an exact re-suggestion match.
    save_analysis(
        [_suggestion("headline", "VP of IT", "VP, Information Technology & Enterprise Architecture")],
        profile_strength_score=65, profile_strength_rationale="improved", analyzed_at="2026-08-08",
    )

    suggestions = load_linkedin_profile()["suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["status"] == "applied"
    # New wording is genuinely adopted, not frozen to the old draft.
    assert suggestions[0]["suggested_text"] == "VP, Information Technology & Enterprise Architecture"


def test_dismissed_status_survives_a_reanalysis(isolated_data):
    save_analysis(
        [_suggestion("about", "20 years in healthcare IT.", "Two decades leading healthcare technology transformation.")],
        profile_strength_score=60, profile_strength_rationale="early days", analyzed_at="2026-08-01",
    )
    first_id = load_linkedin_profile()["suggestions"][0]["id"]
    mark_suggestion_status(first_id, "dismissed")

    save_analysis(
        [_suggestion("about", "20 years in healthcare IT.", "Slightly different reworded pitch.")],
        profile_strength_score=65, profile_strength_rationale="improved", analyzed_at="2026-08-08",
    )

    suggestions = load_linkedin_profile()["suggestions"]
    assert suggestions[0]["status"] == "dismissed"
    assert not get_active_suggestions()


def test_a_genuinely_new_suggestion_still_starts_as_suggested(isolated_data):
    save_analysis(
        [_suggestion("headline", "VP of IT", "VP of Information Technology")],
        profile_strength_score=60, profile_strength_rationale="early days", analyzed_at="2026-08-01",
    )
    mark_suggestion_status(load_linkedin_profile()["suggestions"][0]["id"], "applied")

    # Re-analysis keeps the headline suggestion (still applied) but adds a
    # brand-new one for a different section - that one has no prior
    # identity to match against and must start fresh.
    save_analysis(
        [
            _suggestion("headline", "VP of IT", "VP of Information Technology & Digital Strategy"),
            _suggestion("skills", "", "Add: Enterprise Architecture"),
        ],
        profile_strength_score=70, profile_strength_rationale="better still", analyzed_at="2026-08-08",
    )

    suggestions = {s["section"]: s for s in load_linkedin_profile()["suggestions"]}
    assert suggestions["headline"]["status"] == "applied"
    assert suggestions["skills"]["status"] == "suggested"


def test_new_addition_suggestions_are_matched_by_suggested_text_not_blank_current_text(isolated_data):
    # Two different "propose something new" suggestions in the same
    # section both have empty current_text - they must not collide with
    # each other just because current_text is blank for both.
    save_analysis(
        [
            _suggestion("skills", "", "Add: Enterprise Architecture"),
            _suggestion("skills", "", "Add: Cloud Migration"),
        ],
        profile_strength_score=60, profile_strength_rationale="early days", analyzed_at="2026-08-01",
    )
    by_text = {s["suggested_text"]: s for s in load_linkedin_profile()["suggestions"]}
    mark_suggestion_status(by_text["Add: Enterprise Architecture"]["id"], "applied")

    save_analysis(
        [
            _suggestion("skills", "", "Add: Enterprise Architecture"),
            _suggestion("skills", "", "Add: Cloud Migration"),
        ],
        profile_strength_score=65, profile_strength_rationale="still true", analyzed_at="2026-08-08",
    )

    by_text = {s["suggested_text"]: s for s in load_linkedin_profile()["suggestions"]}
    assert by_text["Add: Enterprise Architecture"]["status"] == "applied"
    assert by_text["Add: Cloud Migration"]["status"] == "suggested"


def test_a_suggestion_dropped_entirely_by_reanalysis_just_disappears(isolated_data):
    # No longer relevant (e.g. the LinkedIn profile changed enough that
    # the AI no longer proposes it) - it should simply not be in the new
    # list, not error or leave orphaned state.
    save_analysis(
        [_suggestion("headline", "VP of IT", "VP of Information Technology")],
        profile_strength_score=60, profile_strength_rationale="early days", analyzed_at="2026-08-01",
    )
    mark_suggestion_status(load_linkedin_profile()["suggestions"][0]["id"], "applied")

    save_analysis(
        [_suggestion("about", "20 years in IT.", "Two decades in enterprise technology leadership.")],
        profile_strength_score=70, profile_strength_rationale="reworked", analyzed_at="2026-08-08",
    )

    suggestions = load_linkedin_profile()["suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["section"] == "about"
