from tailoring.claim_verification import flag_unverified_resume_claims

_PROFILE = {
    "work_history": [
        {"employer": "Kalido Inc.", "title": "Senior Consultant", "start": "2001", "end": "02/2008"},
        {"employer": "SK Life Science, Inc. (SKLSI)", "title": "Head of IT", "start": "01/2024", "end": "01/2026"},
    ],
    "certifications": [{"name": "Reltio MDM Certification", "year": "2017"}],
}


def test_real_employer_and_dates_are_not_flagged():
    resume = "PROFESSIONAL EXPERIENCE\nKalido Inc. - Senior Consultant  Jan 2001 - Feb 2008\n- Did real things."
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume
    assert flagged == []


def test_fabricated_employer_is_flagged():
    resume = "PROFESSIONAL EXPERIENCE\nFabricated Startup Inc. - VP Eng  Jan 2015 - Jan 2020\n- Invented."
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert "Fabricated Startup Inc. - VP Eng  Jan 2015 - Jan 2020?" in marked
    assert flagged == [{"skill": None, "text": "Fabricated Startup Inc. - VP Eng  Jan 2015 - Jan 2020"}]


def test_real_employer_with_impossible_dates_is_flagged():
    # Employer is real, but the drafted years fall entirely outside that
    # employer's real recorded tenure - a genuinely wrong/invented range.
    resume = "PROFESSIONAL EXPERIENCE\nKalido Inc. - Senior Consultant  Jan 2015 - Jan 2020\n- Wrong dates."
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked.splitlines()[1].endswith("?")
    assert len(flagged) == 1


def test_real_employer_with_a_subset_date_range_is_not_flagged():
    # A sub-range within the real tenure (e.g. describing one specific
    # title held partway through a longer stint) must NOT be flagged -
    # year-level containment, not exact-match, is the whole point.
    resume = "PROFESSIONAL EXPERIENCE\nKalido Inc. - Support Analyst  Jan 2002 - Jan 2004\n- Early role."
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume
    assert flagged == []


def test_ongoing_present_role_is_not_flagged_for_end_date():
    resume = "PROFESSIONAL EXPERIENCE\nSK Life Science, Inc. (SKLSI) - Head of IT  Jan 2024 - Present\n- Ongoing."
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume
    assert flagged == []


def test_real_certification_is_not_flagged():
    resume = "CERTIFICATIONS\n- Reltio MDM Certification"
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume
    assert flagged == []


def test_fabricated_certification_is_flagged():
    resume = "CERTIFICATIONS\n- Certified Underwater Basket Weaver"
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked.splitlines()[1] == "- Certified Underwater Basket Weaver?"
    assert flagged == [{"skill": None, "text": "- Certified Underwater Basket Weaver"}]


def test_certification_check_stops_at_the_next_real_section_header():
    # A short, all-caps line that ISN'T a real section header (e.g. an
    # acronym-style certification like "PMP") must not be mistaken for
    # the boundary of the certifications section.
    resume = "CERTIFICATIONS\n- PMP\n\nSKILLS\nPython"
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked.splitlines()[1] == "- PMP?"  # not a real cert -> flagged, section still open
    assert "Python?" not in marked  # SKILLS section is never certification-checked


def test_already_hedged_line_is_never_double_marked():
    resume = "PROFESSIONAL EXPERIENCE\nFabricated Startup Inc. - VP Eng  Jan 2015 - Jan 2020?"
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume  # untouched - already has exactly one "?"
    assert flagged == []


def test_no_work_history_flags_an_unmatchable_employer():
    # Fails open on missing DATA (see the unparseable-start test below),
    # but "no work_history at all" genuinely can't trace back to
    # anything - that's a real, honest "doesn't match" result, not a
    # null-data exemption.
    resume = "PROFESSIONAL EXPERIENCE\nAnywhere Inc. - Role  Jan 2015 - Jan 2020"
    marked, flagged = flag_unverified_resume_claims(resume, {})
    assert flagged == [{"skill": None, "text": "Anywhere Inc. - Role  Jan 2015 - Jan 2020"}]


def test_bare_date_range_on_its_own_line_is_never_flagged():
    # Real false positive found by live-checking Zahir's ACTUAL resume
    # before this shipped (2026-08-09): a common, genuine layout puts the
    # company/location on one line and a bare date range alone on the
    # NEXT ("SK Life Science, Inc. (SKLSI) - Paramus, NJ" / "September
    # 2018 - January 2026"), or a sub-role's title on one line and its own
    # date range on the next without repeating the employer name at all
    # ("Head of IT (CIO-equivalent)" / "January 2024 - January 2026").
    # There's no employer text on the bare date line itself to check -
    # this produced 4 false positives on one real, completely honest
    # resume before the len(prefix) < 3 guard was added. Must never flag,
    # regardless of whether ANY employer in work_history would happen to
    # match the (empty) prefix.
    resume = (
        "PROFESSIONAL EXPERIENCE\n"
        "SK Life Science, Inc. (SKLSI) - Paramus, NJ\n"
        "September 2018 - January 2026\n"
        "Head of IT (CIO-equivalent)\n"
        "January 2024 - January 2026\n"
        "- Real bullet."
    )
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume
    assert flagged == []


def test_matched_employer_with_unparseable_real_start_is_not_flagged():
    # The employer trace-back succeeded (a real match) but that specific
    # profile entry has no parseable start date to check the range
    # against - fails open (don't flag on the app's own incomplete data),
    # not closed.
    profile = {"work_history": [{"employer": "Kalido Inc.", "start": None, "end": None}], "certifications": []}
    resume = "PROFESSIONAL EXPERIENCE\nKalido Inc. - Role  Jan 2015 - Jan 2020"
    marked, flagged = flag_unverified_resume_claims(resume, profile)
    assert marked == resume
    assert flagged == []
