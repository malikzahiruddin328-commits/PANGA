from tailoring.unconfirmed_claims import find_unconfirmed_markers, has_unconfirmed_marker, resolve_unconfirmed_claim


def test_has_unconfirmed_marker_true_when_a_question_mark_is_present():
    assert has_unconfirmed_marker("SKILLS\nLed a team of 8-10 engineers?\n")


def test_has_unconfirmed_marker_false_on_clean_text():
    assert not has_unconfirmed_marker("SKILLS\nLed a team of 8 engineers.\n")


def test_has_unconfirmed_marker_false_on_none_or_empty():
    assert not has_unconfirmed_marker(None)
    assert not has_unconfirmed_marker("")


def test_find_unconfirmed_markers_flags_a_line_in_resume_text():
    app_record = {"resume_text": "PROFESSIONAL EXPERIENCE\nLed a team of 8-10 engineers?\nEDUCATION\nBS"}
    flagged = find_unconfirmed_markers(app_record)
    assert len(flagged) == 1
    assert flagged[0]["field"] == "resume_text"
    assert flagged[0]["line"] == "Led a team of 8-10 engineers?"
    assert flagged[0]["skill"] is None


def test_find_unconfirmed_markers_attaches_the_ai_reported_skill_label():
    app_record = {
        "resume_text": "SKILLS\nLed a team of 8-10 engineers?",
        "resume_unconfirmed_claims_ai_reported": [
            {"skill": "Team size at Acme", "text": "Led a team of 8-10 engineers?"},
        ],
    }
    flagged = find_unconfirmed_markers(app_record)
    assert flagged[0]["skill"] == "Team size at Acme"


def test_find_unconfirmed_markers_falls_back_to_none_when_ai_report_is_stale():
    # Real gap this guards against: the AI's own self-report is a snapshot
    # from the last generate and can't be trusted alone (same "don't trust
    # the AI's own accounting" principle as everywhere else in this app) -
    # a hand-edited "?" the AI never reported must still show up, flagged,
    # rather than being silently invisible to the gate.
    app_record = {
        "resume_text": "SKILLS\nSome hand-typed guess?",
        "resume_unconfirmed_claims_ai_reported": [
            {"skill": "Unrelated fact", "text": "A totally different guess?"},
        ],
    }
    flagged = find_unconfirmed_markers(app_record)
    assert len(flagged) == 1
    assert flagged[0]["skill"] is None


def test_find_unconfirmed_markers_scans_every_prose_field():
    app_record = {
        "resume_text": "clean",
        "cover_letter_text": "Maybe 5 years?",
        "exec_bio_text": "clean",
        "leadership_summary_text": "Something else?",
    }
    flagged = find_unconfirmed_markers(app_record)
    fields = {f["field"] for f in flagged}
    assert fields == {"cover_letter_text", "leadership_summary_text"}


def test_find_unconfirmed_markers_scans_apply_answers_by_label():
    app_record = {
        "apply_answers": [
            {"label": "Desired Salary", "value": "Roughly $180K?"},
            {"label": "Phone Number", "value": "555-1234"},
        ],
    }
    flagged = find_unconfirmed_markers(app_record)
    assert len(flagged) == 1
    assert flagged[0]["field"] == "apply_answers:Desired Salary"


def test_find_unconfirmed_markers_empty_when_nothing_flagged():
    app_record = {"resume_text": "Nothing but real, confirmed facts."}
    assert find_unconfirmed_markers(app_record) == []


def test_resolve_unconfirmed_claim_confirm_strips_only_the_trailing_question_mark(isolated_data):
    job = {"title": "Director", "organization": "Acme"}
    app_record = {"resume_text": "SKILLS\nLed a team of 8-10 engineers?\nEDUCATION\nBS"}
    claim = {"field": "resume_text", "skill": "Team size", "line": "Led a team of 8-10 engineers?"}

    result = resolve_unconfirmed_claim(job, app_record, claim, action="confirm")

    assert result["resume_text"] == "SKILLS\nLed a team of 8-10 engineers\nEDUCATION\nBS"
    from profile.storage import load_profile
    answers = load_profile()["gap_interview_answers"]
    assert answers[0]["skill"] == "Team size"
    assert answers[0]["answer"] == "Led a team of 8-10 engineers"


def test_resolve_unconfirmed_claim_edit_replaces_the_line_with_the_real_fact(isolated_data):
    job = {"title": "Director", "organization": "Acme"}
    app_record = {"resume_text": "SKILLS\nLed a team of 8-10 engineers?\nEDUCATION\nBS"}
    claim = {"field": "resume_text", "skill": "Team size", "line": "Led a team of 8-10 engineers?"}

    result = resolve_unconfirmed_claim(job, app_record, claim, action="edit", new_text="Led a team of exactly 12 engineers.")

    assert result["resume_text"] == "SKILLS\nLed a team of exactly 12 engineers.\nEDUCATION\nBS"
    from profile.storage import load_profile
    answers = load_profile()["gap_interview_answers"]
    assert answers[0]["answer"] == "Led a team of exactly 12 engineers."


def test_resolve_unconfirmed_claim_edit_rejects_text_still_containing_a_question_mark():
    import pytest

    job = {"title": "Director", "organization": "Acme"}
    app_record = {"resume_text": "Led a team of 8-10 engineers?"}
    claim = {"field": "resume_text", "skill": "Team size", "line": "Led a team of 8-10 engineers?"}

    with pytest.raises(ValueError):
        resolve_unconfirmed_claim(job, app_record, claim, action="edit", new_text="Still unsure, maybe 10?")


def test_resolve_unconfirmed_claim_edit_rejects_empty_text():
    import pytest

    job = {"title": "Director", "organization": "Acme"}
    app_record = {"resume_text": "Led a team of 8-10 engineers?"}
    claim = {"field": "resume_text", "skill": "Team size", "line": "Led a team of 8-10 engineers?"}

    with pytest.raises(ValueError):
        resolve_unconfirmed_claim(job, app_record, claim, action="edit", new_text="   ")


def test_resolve_unconfirmed_claim_unknown_action_raises():
    import pytest

    job = {"title": "Director", "organization": "Acme"}
    app_record = {"resume_text": "Led a team of 8-10 engineers?"}
    claim = {"field": "resume_text", "skill": "Team size", "line": "Led a team of 8-10 engineers?"}

    with pytest.raises(ValueError):
        resolve_unconfirmed_claim(job, app_record, claim, action="delete")


def test_resolve_unconfirmed_claim_falls_back_to_the_resolved_line_as_skill_when_ai_never_reported_one(isolated_data):
    job = {"title": "Director", "organization": "Acme"}
    app_record = {"resume_text": "Some hand-typed guess?"}
    claim = {"field": "resume_text", "skill": None, "line": "Some hand-typed guess?"}

    resolve_unconfirmed_claim(job, app_record, claim, action="confirm")

    from profile.storage import load_profile
    answers = load_profile()["gap_interview_answers"]
    assert answers[0]["skill"] == "Some hand-typed guess"


def test_resolve_unconfirmed_claim_resolves_an_apply_answers_entry(isolated_data):
    job = {"title": "Director", "organization": "Acme"}
    app_record = {"apply_answers": [
        {"label": "Desired Salary", "value": "Roughly $180K?"},
        {"label": "Phone Number", "value": "555-1234"},
    ]}
    claim = {"field": "apply_answers:Desired Salary", "skill": None, "line": "Roughly $180K?"}

    result = resolve_unconfirmed_claim(job, app_record, claim, action="confirm")

    updated = result["apply_answers"]
    assert next(i["value"] for i in updated if i["label"] == "Desired Salary") == "Roughly $180K"
    assert next(i["value"] for i in updated if i["label"] == "Phone Number") == "555-1234"
