from tailoring.claim_verification import flag_unverified_resume_claims

_PROFILE = {
    "work_history": [
        {"employer": "Kalido Inc.", "title": "Senior Consultant", "start": "2001", "end": "02/2008"},
        # Real shape (Zahir's actual profile, 2026-08-09): the SAME real
        # employer recorded as TWO separate entries for two different
        # internal titles/sub-ranges - the union-based date check exists
        # specifically to handle this.
        {"employer": "SK Life Science, Inc. (SKLSI)", "title": "Enterprise Architect and Commercial Systems Lead", "start": "09/2018", "end": "02/2020"},
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


def test_education_section_date_ranges_are_never_checked_against_work_history():
    # Real, broad false positive found by RM checking ALL 28 of Zahir's
    # real resumes (2026-08-09), not just the one this module was
    # originally verified against: EDUCATION lines like "Brunel
    # University London, Middlesex, UK, 1997 - 2001" also match
    # DATE_RANGE_RE and were wrongly checked against work_history - a
    # university is never going to be a listed employer. The role-header
    # check must be scoped to the PROFESSIONAL EXPERIENCE section only.
    resume = (
        "EDUCATION\n"
        "Brunel University London, Middlesex, UK, 1997 - 2001\n"
        "Bachelor of Science, Information Systems"
    )
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume
    assert flagged == []


def test_sub_role_line_uses_the_employer_named_on_an_earlier_line():
    # Real, dominant false-positive cause found by RM checking all 28 real
    # resumes: a sub-role line states a title and date but no employer -
    # the employer was already named on an EARLIER line in the same
    # block. Must fall back to that context rather than flag as
    # fabricated. Uses the union-based date check since Zahir's SAME real
    # employer is recorded as two separate work_history entries spanning
    # 2018-2020 and 2024-2026 - a sub-role dated 2024-2026 must pass even
    # though only the SECOND entry alone covers it.
    resume = (
        "PROFESSIONAL EXPERIENCE\n"
        "SK Life Science, Inc. (SKLSI) - Paramus, NJ\n"
        "Head of IT (CIO-equivalent), January 2024 - January 2026\n"
        "- Real bullet."
    )
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume
    assert flagged == []


def test_sub_role_line_with_impossible_dates_is_still_flagged_via_employer_context():
    # The employer-context fallback must still catch a genuinely wrong
    # date range, not blanket-exempt every contextual sub-role line.
    resume = (
        "PROFESSIONAL EXPERIENCE\n"
        "SK Life Science, Inc. (SKLSI) - Paramus, NJ\n"
        "Head of IT, January 1990 - January 1995\n"
    )
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked.splitlines()[2].endswith("?")
    assert len(flagged) == 1


def test_employer_context_does_not_leak_across_a_different_real_employer():
    # A new real employer line must override the previous context, not
    # accumulate - a sub-role after the SECOND employer's line must be
    # checked against the SECOND employer, not the first.
    resume = (
        "PROFESSIONAL EXPERIENCE\n"
        "Kalido Inc. - Boston, MA\n"
        "Senior Consultant, Jan 2001 - Feb 2008\n"
        "SK Life Science, Inc. (SKLSI) - Paramus, NJ\n"
        "Head of IT, January 2024 - January 2026"
    )
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume
    assert flagged == []


def test_bullet_mentioning_a_product_sharing_a_past_employers_name_does_not_reset_context():
    # Real, subtle false-positive cause found by RM checking all 28 real
    # resumes (2026-08-09): "Reltio" is both a real PAST employer (see
    # _PROFILE) and a real MDM software product Zahir uses at his CURRENT
    # job - a bullet mentioning "Reltio MDM" as a tool wrongly reset the
    # employer context from "SK Life Science..." to "Reltio," causing the
    # NEXT two real, honest sub-role dates to be checked against the
    # wrong employer's span and falsely flagged. Only a structural
    # company/location header line (never a bullet) may set the context -
    # bullets are free-form prose that can mention anything.
    resume = (
        "PROFESSIONAL EXPERIENCE\n"
        "SK Life Science, Inc. (SKLSI) - Paramus, NJ\n"
        "Head of IT, January 2024 - January 2026\n"
        "- Ran the commercial technology portfolio including Reltio MDM for HCP/HCO mastering.\n"
        "Vice President, Head of Applications, March 2020 - December 2023\n"
        "- Real bullet."
    )
    profile = {
        "work_history": [
            {"employer": "Reltio", "title": "Customer Success Architect", "start": "05/2017", "end": "09/2018"},
            {"employer": "SK Life Science, Inc. (SKLSI)", "title": "Head of IT", "start": "01/2024", "end": "01/2026"},
            {"employer": "SK Life Science, Inc. (SKLSI)", "title": "VP, Applications", "start": "03/2020", "end": "12/2023"},
        ],
        "certifications": [],
    }
    marked, flagged = flag_unverified_resume_claims(resume, profile)
    assert marked == resume
    assert flagged == []


def test_certification_acronym_expansion_is_not_flagged():
    # Real gap found by RM (2026-08-09): a legitimate acronym EXPANSION
    # ("Reltio Master Data Management (MDM) Certification" for profile's
    # "Reltio MDM Certification") failed literal skills_match phrase
    # containment even though it's the same real credential, just
    # spelled out - not a fabrication.
    resume = "CERTIFICATIONS\n- Reltio Master Data Management (MDM) Certification"
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked == resume
    assert flagged == []


def test_certification_with_no_shared_acronym_is_still_flagged():
    # The acronym fallback must not become a blanket exemption for
    # anything containing parentheses.
    resume = "CERTIFICATIONS\n- Certified Underwater Basket Weaving (CUBW)"
    marked, flagged = flag_unverified_resume_claims(resume, _PROFILE)
    assert marked.splitlines()[1].endswith("?")
    assert len(flagged) == 1


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
