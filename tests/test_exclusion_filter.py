from datetime import datetime, timedelta, timezone

import pytest

from search import exclusion_filter
from security.crypto_store import read_json, write_json


def _job(title, source="Dice", job_id="1", organization="Acme", location="Remote"):
    return {"source": source, "job_id": job_id, "title": title, "organization": organization, "location": location}


# --- Layer 1: seniority-tier exclusion --------------------------------------

@pytest.mark.parametrize("title", [
    "Sr. Systems Engineer",
    "Senior Systems Engineer",
    "Data Scientist",
    "Business Analyst",
    "IT Specialist",
    "Management Consultant",
    "Customer Representative",
    "Project Coordinator",
    "Associate Product Manager",  # "Associate" IC word, no qualifier present
])
def test_seniority_layer_excludes_ic_tier_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result == {
        "rule": "seniority_mismatch",
        "reason": "individual-contributor-tier title with no executive-qualifying word present",
    }


@pytest.mark.parametrize("title", [
    "Senior Director",
    "Director, IT Service Continuity",
    "Associate Director",  # Director qualifies "Associate" the IC word
    "VP Information Technology",
    "Vice President, Engineering",
    "Head of IT",
    "Chief Technology Officer",
    "President, North America",
    "SVP, Data & Analytics",
    "EVP, Technology",
])
def test_seniority_layer_keeps_executive_qualified_titles(title):
    assert exclusion_filter.check_exclusion(_job(title)) is None


def test_seniority_layer_does_not_match_chief_inside_an_unrelated_word():
    # "mischief" contains "chief" as a substring but must not count as the
    # executive qualifier - word-boundary regression guard.
    result = exclusion_filter.check_exclusion(_job("Engineer of Mischief Management"))
    assert result is not None
    assert result["rule"] == "seniority_mismatch"


def test_seniority_layer_does_not_match_head_inside_headquarters():
    result = exclusion_filter.check_exclusion(_job("Analyst, Corporate Headquarters"))
    assert result is not None
    assert result["rule"] == "seniority_mismatch"


# --- Layer 2: clinical/medical domain exclusion -----------------------------

@pytest.mark.parametrize("title", [
    "Senior Medical Director, Hematology Clinical Development",
    "Medical Director",
    "Physician - Internal Medicine",
    "Nurse Practitioner",
    "Registered Nurse - ICU",
    "Clinical Research Director - Immunology",
    "Director, Clinical Pharmacology",
    "Medical Science Liaison - Dermatology",
    "Medical Advisor, Oncology",
])
def test_clinical_layer_excludes_medical_domain_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "clinical_domain"


def test_title_matching_both_layers_is_still_excluded():
    # "Clinical Scientist" carries "Scientist" (an IC-tier noun with no
    # executive qualifier present) AND matches the clinical pattern - both
    # layers independently agree to exclude it. Layer 1 is checked first in
    # check_exclusion(), so the reported rule is seniority_mismatch here,
    # not clinical_domain - what matters is that the job is excluded
    # either way, not which rule's label wins the race.
    result = exclusion_filter.check_exclusion(_job("Clinical Scientist, Oncology"))
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "clinical_domain")


@pytest.mark.parametrize("title", [
    "Laboratory Technician",
    "Veterinary Laboratory Technician",  # real Beacon Hill Life Sciences job
    "Quality Control Laboratory Technician",  # real Beacon Hill Life Sciences job
    "Lab Technician – Powdered Metals",  # real Beacon Hill Life Sciences job
    "Engineering Lab Technician",  # real Beacon Hill Life Sciences job
    "Senior Lab Tech",
])
def test_clinical_layer_excludes_lab_technician_titles(title):
    # Since experiment/filter-quality-improvements added "technician" to
    # layer 1's IC-tier noun list (2026-08-17), every one of these titles
    # now also matches layer 1 (no exec qualifier present) and layer 1 runs
    # first in check_exclusion() - same "excluded either way, rule label
    # isn't the point" situation as test_title_matching_both_layers_is_still_excluded()
    # and test_front_desk_coordinator_excluded_by_seniority_layer_first()
    # elsewhere in this file. What matters is the job is still excluded.
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "clinical_domain")


@pytest.mark.parametrize("title", [
    "Lab Compute Analyst (all genders)",  # real AbbVie job - IT role, not a technician
    "Lab Compute Senior Analyst (all genders)",  # real AbbVie job
    "Cloud/Infrastructure Technician - DHA",  # real USAJOBS job
    "Operating Systems Technician",  # real USAJOBS job
    "IT Support Technician I",  # real U.S. Courts job
    "IT Technician II",  # real U.S. Courts job
    "R&D Engineering Technician",  # real Beacon Hill job - technician, but not lab-titled
    "Technician, Equipment Engineering",  # real AbbVie job
    "Scientist I - Laboratory Staff - Analytical Development",  # real AbbVie job - "Laboratory Staff", not "Laboratory Technician"
])
def test_clinical_layer_does_not_catch_non_lab_technician_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is None or result["rule"] != "clinical_domain"


def test_clinical_layer_fires_even_when_seniority_would_have_kept_it():
    # "Medical Director" carries "Director", which would satisfy the
    # seniority layer's exec-qualifier check on its own - layer 2 must
    # still exclude it independently.
    result = exclusion_filter.check_exclusion(_job("Senior Medical Director, Hematology Clinical Development"))
    assert result["rule"] == "clinical_domain"


def test_known_good_examples_are_kept():
    # The exact real-data validation examples from the request.
    assert exclusion_filter.check_exclusion(_job("Director, IT Service Continuity", organization="AbbVie")) is None


def test_known_bad_examples_are_excluded():
    assert exclusion_filter.check_exclusion(
        _job("Senior Medical Director, Hematology Clinical Development", organization="AbbVie")
    )["rule"] == "clinical_domain"
    assert exclusion_filter.check_exclusion(
        _job("Sr. Systems Engineer", organization="AbbVie")
    )["rule"] == "seniority_mismatch"


def test_no_exclusion_for_a_plausible_unrelated_title():
    assert exclusion_filter.check_exclusion(_job("Chief Information Officer")) is None


# --- Layer 3: custom user exclusions (2026-08-13, Settings tab build) ------

def test_custom_exclusion_matches_case_insensitive_substring():
    result = exclusion_filter.check_exclusion(
        _job("Senior Program Director, Clinical Ops"),
        custom_exclusions=["program director"],
    )
    assert result == {
        "rule": "custom_user_exclusion",
        "reason": 'matched custom excluded term "program director"',
    }


def test_custom_exclusion_is_a_free_form_substring_match_not_word_boundary():
    # Deliberately NOT \b-bounded like the built-in layers - a non-technical
    # user typing a fragment expects plain "contains this text", e.g. "CISO"
    # should catch "Deputy CISO" and "intern" should catch "Internship".
    assert exclusion_filter.check_exclusion(
        _job("Deputy CISO"), custom_exclusions=["CISO"],
    )["rule"] == "custom_user_exclusion"
    assert exclusion_filter.check_exclusion(
        _job("Summer Internship Program"), custom_exclusions=["Intern"],
    )["rule"] == "custom_user_exclusion"


def test_custom_exclusion_empty_list_has_no_effect():
    assert exclusion_filter.check_exclusion(
        _job("Director, IT Service Continuity"), custom_exclusions=[],
    ) is None


def test_custom_exclusion_term_matching_nothing_has_no_effect_and_no_error():
    assert exclusion_filter.check_exclusion(
        _job("Director, IT Service Continuity"),
        custom_exclusions=["Underwater Basket Weaving Lead"],
    ) is None


def test_custom_exclusion_handles_blank_and_whitespace_only_terms_gracefully():
    # A term list with stray empty strings (shouldn't happen given
    # ui/app.py's save-time cleaning, but this is a public function other
    # callers could hit directly) must not raise or match everything.
    assert exclusion_filter.check_exclusion(
        _job("Director, IT Service Continuity"),
        custom_exclusions=["", "   ", "Underwater Basket Weaving Lead"],
    ) is None


def test_custom_exclusion_still_fires_when_built_in_layers_pass():
    # "Program Director" alone passes both built-in layers (Director
    # qualifies past seniority, no clinical match) but is now caught by
    # layer 6's PM-track pattern regardless - what matters here is layer 3
    # can independently exclude it too when the user's own term matches.
    result = exclusion_filter.check_exclusion(
        _job("Program Director"), custom_exclusions=["Program Director"],
    )
    assert result["rule"] == "custom_user_exclusion"


def test_check_exclusion_defaults_to_loading_custom_exclusions_from_settings(isolated_data):
    exclusion_filter.SETTINGS_PATH.write_text(
        "custom_title_exclusions:\n- Program Director\n- CISO\n", encoding="utf-8",
    )
    result = exclusion_filter.check_exclusion(_job("Deputy CISO"))
    assert result["rule"] == "custom_user_exclusion"


def test_load_custom_title_exclusions_missing_file_returns_empty_list(isolated_data):
    assert exclusion_filter.load_custom_title_exclusions() == []


def test_load_custom_title_exclusions_missing_key_returns_empty_list(isolated_data):
    exclusion_filter.SETTINGS_PATH.write_text("industries:\n- Pharma\n", encoding="utf-8")
    assert exclusion_filter.load_custom_title_exclusions() == []


def test_load_custom_title_exclusions_reads_configured_terms(isolated_data):
    exclusion_filter.SETTINGS_PATH.write_text(
        "custom_title_exclusions:\n- Project Manager\n- Intern\n", encoding="utf-8",
    )
    assert exclusion_filter.load_custom_title_exclusions() == ["Project Manager", "Intern"]


# --- Layer 4: generic administrative/clerical/demo-support exclusion -------

@pytest.mark.parametrize("title", [
    "Administrative Assistant",
    "Senior Administrative Assistant",
    "Senior Administrative Assistant, IMCO, Eyecare & Specialty",  # real AbbVie job
    "Executive Assistant",
    "Executive Assistant to the CEO",
    "Office Manager",
    "Receptionist",
    "Value Proposition and Demonstration Manager - Onco Solids",  # real AbbVie job
    "Demonstration Manager",
    "Value Proposition Manager",
])
def test_admin_support_layer_excludes_generic_non_technical_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "administrative_support_role"


def test_front_desk_coordinator_excluded_by_seniority_layer_first():
    # "Front Desk Coordinator" also matches this layer's own pattern, but
    # layer 1 (Coordinator = IC-tier noun, no exec qualifier) runs first in
    # check_exclusion() and claims it - same "excluded either way, rule
    # label isn't the point" situation as the existing
    # test_title_matching_both_layers_is_still_excluded() case above.
    result = exclusion_filter.check_exclusion(_job("Front Desk Coordinator"))
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "administrative_support_role")


@pytest.mark.parametrize("title", [
    "Systems Administrator",
    "Database Administrator",
    "Network Administrator",
    "Operational Technology Systems Administrator",  # real AbbVie job
    "FVP/SVP, Credit Administrator",  # real AbbVie job - "Administrator" != "Administrative Assistant"
    "Associate CIO, Administrative Applications",  # real AbbVie job - "Administrative Applications" != "Administrative Assistant"
    "IT Office Manager",  # hedge: tech-qualified variant of a caught phrase
    "Digital Demonstration Manager",  # hedge: tech-qualified variant of a caught phrase
    "Chief Information Officer",
    "Director, IT Service Continuity",
    "VP Information Technology",
])
def test_admin_support_layer_does_not_catch_technical_or_unrelated_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is None or result["rule"] != "administrative_support_role"


def test_admin_support_layer_real_slipped_through_examples():
    # The exact two real jobs Zahir flagged as having slipped through
    # unfiltered from AbbVie's company-site pull.
    assert exclusion_filter.check_exclusion(
        _job("Value Proposition and Demonstration Manager - Onco Solids", organization="AbbVie")
    )["rule"] == "administrative_support_role"
    assert exclusion_filter.check_exclusion(
        _job("Senior Administrative Assistant", organization="AbbVie")
    )["rule"] == "administrative_support_role"


# --- Layer 5: hands-on IC engineering/architecture title exclusion ---------

# These carry a VP/SVP/Vice-President exec-qualifying word, so layer 1
# (seniority_mismatch) does NOT claim them first - only layer 5 catches
# them, so the rule label itself is the thing under test here (this is
# exactly the real gap: a bare "Software Engineer" is already caught by
# layer 1, but "SVP, Full-Stack Engr" bypasses layer 1 entirely because
# "SVP" is an exec-qualifying word, so without this layer it would have
# reached Zahir unfiltered).
@pytest.mark.parametrize("title", [
    "SVP, Full-Stack Engr",  # real BNY Mellon job, via Dice
    "VP, Full-Stack Engr II",  # real BNY Mellon job, via Dice
    "Senior Full Stack Engineer, ATM Platforms - VP",  # real Citi job, via SimplyHired
    "SVP Lead Full Stack Engineer (M&A Technology & AI)",  # real Citi job
    "SVP, Principal Full Stack Engineer - Performance Product Engineering",  # real, via Dice
    "SVP, Infra Engineering (Mainframe Comms Systems Software Engineer)",  # real, via Dice
    "Senior Front End Engineer, VP",  # real Citi job, via SimplyHired
    "SVP Senior KDB+ Platform Engineer",  # real, via Dice
    "Corporate Vice President - Google Cloud Platform Engineer",  # real, via Built In
    "Lead .Net Platform Engineer (Payments Ingestion) - Senior Vice President",  # real, via SimplyHired
    "ATM Architect",  # real Dice job - "architect" isn't an IC-tier noun for layer 1, so layer 5 is the only layer that ever catches this one, prefix or not
    "VP Full Stack Engineer",
    "VP Backend Engineer",
    "VP Frontend Engineer",
    "VP Platform Engineer",
])
def test_ic_engineer_layer_excludes_vp_prefixed_titles_layer_1_would_miss(title):
    # custom_exclusions=[] isolates this from Zahir's real Settings-tab
    # list (config/settings.yaml already has "Mainframe" configured, which
    # would otherwise claim "SVP, Infra Engineering (Mainframe Comms
    # Systems Software Engineer)" via layer 3 before layer 5 ever runs -
    # a real environment-state leak, not a layer 5 bug).
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is not None
    assert result["rule"] == "ic_engineer_title"


# These have no VP/SVP/exec-qualifying prefix, so layer 1
# (seniority_mismatch, pre-existing) already excludes them - still
# confirms the layer 5 phrase pattern itself matches every named role
# noun (full-stack, software, backend, frontend, platform engineer),
# independent of which layer's label ends up claiming the exclusion.
@pytest.mark.parametrize("title", [
    "Full Stack Engineer",
    "Full-Stack Engineer",
    "Software Engineer",
    "Backend Engineer",
    "Back-End Engineer",
    "Frontend Engineer",
    "Front-End Engineer",
    "Platform Engineer",
])
def test_ic_engineer_role_pattern_matches_every_named_phrase_directly(title):
    assert exclusion_filter._IC_ENGINEER_ROLE_PATTERN.search(title) is not None
    # And the title is excluded end-to-end either way (via layer 1 or 5).
    assert exclusion_filter.check_exclusion(_job(title)) is not None


@pytest.mark.parametrize("title", [
    "VP of Engineering",
    "Vice President of Engineering",
    "Director of Software Engineering",
    "Head of Platform Engineering",
    "Head of Engineering",
    "SVP of Engineering",
    "Chief Technology Officer",
    "CTO",
    "Engineering Manager",
    "Software Engineering Manager",
    "Software Engineering Manager, SVP - AI Platform Development",  # real, via Dice
    "Senior Engineering Manager, AI Agents",  # real, via Dice
    "Corporate Vice President - Cloud and Integrations Engineering Manager - Field Experience",  # real
    "Director, Software Engineering",
    "Global Head of Data Platform Engineering, SVP",  # real, via Dice - "Platform Engineering" != "Platform Engineer"
    "Sr. Director, Platform Engineering",  # real, via SimplyHired
    "Executive Director, Digital Transformation & Platform Engineering",  # real, via Indeed
    "Chief Architect, Decision Intelligence (Remote)",  # real - generic "architect", not "atm architect"
    "Salesforce CTO Architect",  # real - generic "architect", not "atm architect"
    "Solutions Architect",  # real - generic "architect", not "atm architect"
    "Enterprise Architect",
    "Director IT Architecture",
    "Chief Information Officer",
    "Vice President, Information Technology",
    "IT Director, Software Engineering & Integration",  # real, via Dice - "Software Engineering" != "Software Engineer"
])
def test_ic_engineer_layer_does_not_catch_leadership_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is None or result["rule"] != "ic_engineer_title"


def test_ic_engineer_layer_manager_exemption_even_if_engineer_noun_present():
    # Belt-and-suspenders: even a hypothetical title carrying the bare
    # "engineer" noun AND "Manager" is exempted, since "Manager" signals
    # people-management, not hands-on IC work.
    result = exclusion_filter.check_exclusion(_job("Software Engineer Manager, SVP"))
    assert result is None or result["rule"] != "ic_engineer_title"


def test_ic_engineer_layer_real_bny_mellon_and_citi_examples():
    # The exact four real jobs Zahir flagged as slipping through despite
    # senior-sounding VP/SVP prefixes - banking pay-grade, not leadership.
    assert exclusion_filter.check_exclusion(
        _job("SVP, Full-Stack Engr", organization="Bank of New York Mellon")
    )["rule"] == "ic_engineer_title"
    assert exclusion_filter.check_exclusion(
        _job("VP, Full-Stack Engr II", organization="Bank of New York Mellon")
    )["rule"] == "ic_engineer_title"
    assert exclusion_filter.check_exclusion(
        _job("Senior Full Stack Engineer, ATM Platforms - VP", organization="Citi")
    )["rule"] == "ic_engineer_title"
    assert exclusion_filter.check_exclusion(
        _job("SVP Lead Full Stack Engineer", organization="Citi")
    )["rule"] == "ic_engineer_title"


# --- Layer 1 extension (2026-08-17): administrator/technician/IT-SPEC ------

@pytest.mark.parametrize("title", [
    "Operational Technology Systems Administrator",  # real, via Dice
    "Cloud/Infrastructure Technician - DHA",  # real USAJOBS job
    "Operating Systems Technician",  # real USAJOBS job
    "IT Support Technician I",  # real U.S. Courts job
    "IT Technician II",  # real U.S. Courts job
    "Temporary Information Technology Technician II  1 Year Term",  # real U.S. Courts job
    "IT SPEC (INFOSEC)",  # real Air National Guard Units job
    "IT SPEC (INFOSEC/NETWORK) - TITLE 32",  # real Air National Guard Units job
    "ITSPEC (INFOSEC/NETWORK) (Title 32)",  # real Air National Guard Units job - unspaced variant
    "ITSPEC (NETWORK)",  # real Air National Guard Units job - unspaced variant
])
def test_seniority_layer_excludes_new_ic_tier_nouns(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "seniority_mismatch"


@pytest.mark.parametrize("title", [
    "Director, IT Service Continuity",
    "Chief Information Officer",
    "VP Information Technology",
    "Director, Systems Administration",  # "Administration" != "Administrator", also Director-qualified
])
def test_seniority_layer_new_nouns_still_exempt_exec_qualified_titles(title):
    assert exclusion_filter.check_exclusion(_job(title)) is None


@pytest.mark.parametrize("title", [
    "Systems Administrator",
    "Database Administrator",
    "Network Administrator",
])
def test_seniority_layer_bare_administrator_now_excluded_same_as_engineer_analyst(title):
    # Deliberate behavior change from master, not a bug: on master, a bare
    # "Administrator" title with no seniority prefix at all was silently
    # KEPT (never in layer 1's noun list), while an equally IC-tier bare
    # "Systems Engineer"/"Business Analyst"/"IT Specialist" was already
    # excluded - an inconsistency in what "IC-tier, no leadership signal"
    # meant depending only on which noun the posting happened to use.
    # "Administrator" now gets the identical treatment: excluded only when
    # BOTH true (IC-tier noun present AND no exec qualifier), same as
    # every other noun in this list, so "FVP/SVP, Credit Administrator"-
    # shaped titles (qualifier present) are unaffected. Validated against
    # the full live store (2026-08-17): only 3 real bare "Administrator"
    # titles with no exec qualifier exist ("Operational Technology Systems
    # Administrator" via Dice, "Cloud Systems Administrator"/"Database
    # Administrator-DHA" at Social Security Administration/USAJOBS), all
    # genuinely non-leadership IC roles - zero real titles lose leadership-
    # relevant coverage from this change.
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "seniority_mismatch"


def test_seniority_layer_cio_exec_qualifier_gap_since_fixed_on_master():
    # "Associate CIO, Administrative Applications" (real AbbVie job) used to
    # be wrongly excluded here: "CIO" was never in _EXEC_QUALIFIER_PATTERN,
    # so "Associate" (an IC-tier noun) had no qualifying word to exempt it
    # against - a false-POSITIVE-risk bug this branch originally flagged as
    # out of its own scope (see git history on this test, pre-merge). Master
    # independently fixed the actual gap the same day (added "cio"/"ciso"/
    # "cto" to _EXEC_QUALIFIER_PATTERN, see layer 1's docstring above) - that
    # fix landed in this branch via the merge from master into
    # feature/pharma-regulatory-exclusions, so the title is now correctly
    # kept, not excluded. Updated to assert the current, correct behavior
    # rather than continue documenting a gap that no longer exists.
    result = exclusion_filter.check_exclusion(_job("Associate CIO, Administrative Applications"))
    assert result is None


def test_non_it_commercial_layer_catches_real_credit_administrator_role_previously_missed():
    # "FVP/SVP, Credit Administrator" (real Cathay Bank job, live store) is
    # a bank credit/lending-operations role, not IT - it used to slip
    # through every one of the seven prior layers untouched (confirmed
    # against master directly: check_exclusion() returned None). Layer 6's
    # new \bcredit\b match now correctly catches it.
    result = exclusion_filter.check_exclusion(_job("FVP/SVP, Credit Administrator", organization="Cathay Bank"))
    assert result is not None
    assert result["rule"] == "non_it_commercial_role"


# --- Layer 2 extension (2026-08-17): pharma CMC / clinical-operations ------

@pytest.mark.parametrize("title", [
    "Scientific Technical Lead, Early Stage PDST CMC",  # real AbbVie job
    "Director of CMC, External Operations",  # real AbbVie job
    "Associate Director, RA CMC",  # real AbbVie job
    "Scientific Technical Lead, Late Stage CMC",  # real AbbVie job
    "Clinical Site Lead",  # real GForce Life Sciences job
    "Associate Director, Clinical Operations Compliance & Training",  # real NexInfo Solutions job
    "Director, Country Clinical Operations Management",  # real AbbVie job
    "Director, Clinical Process Excellence",  # real AbbVie job
    "Clinical Psychologist / Director of Clinical Services",  # real Project Vida job
])
def test_clinical_layer_excludes_cmc_and_clinical_ops_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "clinical_domain"


@pytest.mark.parametrize("title", [
    "Director, IT Service Continuity",
    "Vice President, Clinical Technology and Integration",  # real Nuvodia job - health-IT adjacent, kept deliberately
    "Chief Information Officer",
])
def test_clinical_layer_cmc_extension_does_not_catch_unrelated_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is None or result["rule"] != "clinical_domain"


# --- Layer 5 extension (2026-08-17): bare "developer" role noun ------------

@pytest.mark.parametrize("title", [
    "Senior Java Developer, FX eCommerce, Vice President",  # real Citi job
    "Lead Java Developer - Senior Vice President",  # real Citi job
    "COBOL Developer",  # real USAJOBS job
    "Python Developer",  # real USAJOBS job
    "PEGA Developer- DHA",  # real USAJOBS job
    "Business Intelligence Platform Developer",  # real USAJOBS job
    "Node-React Full Stack Web Application Developer",  # real USAJOBS job
])
def test_ic_engineer_layer_excludes_developer_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "ic_engineer_title")


def test_ic_engineer_layer_developer_manager_exemption():
    # "SVP, Senior Oracle AML Manager/Developer" (real Jefferies job) -
    # carries both "Developer" and "Manager" - the existing
    # _PEOPLE_MANAGEMENT_PATTERN exemption keeps it, same as it already
    # does for "Software Engineering Manager"-shaped titles.
    result = exclusion_filter.check_exclusion(_job("SVP, Senior Oracle AML Manager/Developer"))
    assert result is None or result["rule"] != "ic_engineer_title"


def test_ic_engineer_layer_developer_vp_prefix_layer_1_would_miss():
    # Isolates layer 5 specifically: "Vice President" is an exec qualifier
    # that would make layer 1 keep this title, so only layer 5's new
    # "developer" noun (no VP/SVP exemption) catches it.
    result = exclusion_filter.check_exclusion(
        _job("Senior Java Developer, FX eCommerce, Vice President", organization="Citi")
    )
    assert result["rule"] == "ic_engineer_title"


# --- Layer 9 (new, 2026-08-17): non-IT commercial/finance leadership -------

@pytest.mark.parametrize("title", [
    "Senior VP Sales",  # real Dynamic Drain Technologies job
    "Vice President of Sales and Marketing",  # real Car Keys Express job
    "SVP, Channel & Independent Agency Sales",  # real Genius Sports job
    "VP of Finance",  # real Hutton job
    "Vice President Finance",  # real SESLOC Credit Union job
    "Director, Finance Internal Audit",  # real AbbVie job
    "SVP, Bond Quant (Corporate Credit) - Risk Management",  # real Jefferies job
    "Senior VP Credit Portfolio Manager",  # real SouthPoint Bank job
    "J.P. Morgan Wealth Management - Vice President, Private Wealth Planner",  # real JPMorganChase job
    "Business Strategy Wealth Management Operations - Vice President",  # real JPMorganChase job
    "Commercial Card Product Solutions Manager- Payments - Vice President",  # real JPMorganChase job
    "Card Fraud Strategy - Vice President",  # real JPMorganChase job
])
def test_non_it_commercial_layer_excludes_pure_commercial_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "non_it_commercial_role"


@pytest.mark.parametrize("title", [
    "Vice President, Finance – CIO North America",  # real JPMorganChase job - genuine Finance-division CIO
    "IT Infrastructure Managed Services - Sales Director",  # real PwC job
    "Sr. Director, IT Product Management – Post Sales and Partner Technology",  # real MongoDB job
    "IT Director - Finance and Procurement App",  # real Novolex job
    "Vice President of Finance & IT",  # real Grupo Mariposa job
    "Director II, IT Service Owner - ERP & Finance Services",  # real BAE Systems job
    "VP Technology - Finance",  # real Crawford & Company job
    "Chief Information Officer",
    "Director, IT Service Continuity",
])
def test_non_it_commercial_layer_does_not_catch_it_qualified_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is None or result["rule"] != "non_it_commercial_role"


def test_non_it_commercial_layer_real_jpmorgan_examples():
    assert exclusion_filter.check_exclusion(
        _job("Senior VP Sales", organization="Dynamic Drain Technologies")
    )["rule"] == "non_it_commercial_role"
    assert exclusion_filter.check_exclusion(
        _job("Vice President, Finance – CIO North America", organization="JPMorganChase")
    ) is None


# --- Layer 6: project/program/product management track exclusion -----------

@pytest.mark.parametrize("title", [
    "Project Director",  # real title, Neil Hoosier & Associates
    "DHS PROGRAM DIRECTOR 4 - 79704",  # real title
    "VP of Product Management, Monetization",  # real title, Yelp
    "Head of Product Management – Intelligence Ventures",  # real title, SPECTRUM
    "Project Manager",
    "Senior Project Manager",
    "Program Manager",
    "IT Program Manager",
    "IT Project Manager",
    "Director, Product Management",
    "Director of Product Management, Hardware",
    "Product Director",
    "Vice President, Program Management Office",
    "Head of Program Management Office (PMO)",
])
def test_pm_track_layer_excludes_pm_pgm_prodm_titles(title):
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is not None
    assert result["rule"] == "pm_track_mismatch"


def test_pm_track_layer_excludes_even_when_seniority_layer_would_win_the_label():
    # "IT PMO Consultant..." also carries "Consultant" (an IC-tier noun
    # with no executive qualifier), so layer 1 (checked first) reports
    # seniority_mismatch - same "which label wins the race doesn't matter,
    # both layers agree to exclude" situation as "Clinical Scientist"
    # above. What matters is the job is excluded either way.
    result = exclusion_filter.check_exclusion(
        _job("IT PMO Consultant - Project Governance & Portfolio Management"),
        custom_exclusions=[],
    )
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "pm_track_mismatch")


@pytest.mark.parametrize("title", [
    "Director, IT Service Continuity",  # real validated KEEP, AbbVie
    "IT Director, Vendor Management",
    "Director, Product Engineering",  # "Director" precedes "Product" - different sense
    "Chief Information Officer (CIO)",
    "Head of IT",
    "VP Information Technology",
])
def test_pm_track_layer_does_not_catch_it_leadership_titles(title):
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is None or result["rule"] != "pm_track_mismatch"


# --- Layer 7: intern/internship exclusion ------------------------------------

@pytest.mark.parametrize("title", [
    "Intern - Biotechnologist (Protein)",  # real title
    "Fall 2026 IT Intern (Incident Responder)",  # real title
    "2027 Accounting & Finance Development Program Intern (Undergraduate)",  # real title
    "Interested in an internship?",  # real title, junk/vague
    "Summer IT Intern",
])
def test_intern_layer_excludes_intern_titles(title):
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is not None
    assert result["rule"] == "intern_role"


def test_intern_layer_does_not_catch_a_role_directing_an_internship_program():
    # Real title in the live store - this role DIRECTS an internship
    # program, it isn't an intern position, and carries "Director" as an
    # executive qualifier, same exemption shape as layer 1.
    result = exclusion_filter.check_exclusion(
        _job("Dietitian (Dietetic Internship Director)"), custom_exclusions=[],
    )
    assert result is None or result["rule"] != "intern_role"


# --- Layer 8: security-domain exclusion (broadened 2026-08-13) --------------

@pytest.mark.parametrize("title", [
    "VP, Information Security and Compliance",  # real title, Veritone Corp
    "Director of Information Security (Hybrid)",  # real title, SAGE Dining Services
    "Information Security Manager",
    "VP of Information Security",
    "Vice President, Information Security",
    "Director, IT, Information Security & Data Privacy",
    "Service Information Security Officer (SISO)",  # real title - domain officer, not chief-executive
])
def test_information_security_layer_excludes_domain_titles(title):
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is not None
    assert result["rule"] == "information_security_domain"


@pytest.mark.parametrize("title", [
    # Real live-store titles containing "security" but NOT "information
    # security" - the broadened-scope examples Zahir's option-B choice
    # (2026-08-13) explicitly requires excluding, including combined
    # IT+Security leadership titles.
    "Director of IT Platforms & Security",
    "Director of Infrastructure and Security",
    "Director, IT & Security",
    "Director, Information Technology & Security",
    "Head of Cyber Security",
    "Head of Infrastructure & Security",
    "IT Security Director",
    "SVP, Network Security Engineering Lead",
    "Security Director (Governance, Risk & Compliance)",
    "VP HR, Safety & Security",
    "Vice President, Software Supply Chain Security",
])
def test_information_security_layer_excludes_any_title_containing_security(title):
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is not None
    assert result["rule"] == "information_security_domain"


@pytest.mark.parametrize("title", [
    # These real live-store titles also carry an IC-tier noun with no
    # executive qualifier ("Engineer"/"Officer"), so layer 1 (checked
    # first in check_exclusion()) wins the label - same "which rule wins
    # the race doesn't matter, both layers agree to exclude" situation as
    # "Clinical Scientist" above. What matters is the job is excluded
    # either way under the broadened security pattern.
    "API Security Engineer",
    "Transportation Security Officer",
])
def test_information_security_layer_excludes_even_when_seniority_wins_the_label(title):
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "information_security_domain")


@pytest.mark.parametrize("title", [
    # "Analyst"/"Specialist" are also layer 1 IC-tier nouns with no
    # executive qualifier - same "which label wins the race doesn't matter,
    # both layers agree to exclude" situation as "Clinical Scientist" above.
    "Information Security Analyst",
    "Information Security Specialist",
    "IT Spec (Infosec), GS-2210-14, FPL 14 (DH) (Open-Continuous)",  # real title, "infosec" variant
])
def test_information_security_layer_excludes_even_when_another_layer_would_win_the_label(title):
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "information_security_domain")


@pytest.mark.parametrize("title", [
    "Chief Information Officer (CIO)",
    "Chief Information Officer",
])
def test_information_security_layer_does_not_catch_cio_titles(title):
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is None or result["rule"] != "information_security_domain"


@pytest.mark.parametrize("title", [
    # Zahir explicitly overrode the original CISO carve-out (2026-08-13):
    # "ciso and security... must be excluded from the initial fetch" - CISO
    # is a real disqualifier per his own profile data
    # (master_profile.json), not just a lower-fit_score preference, so
    # these must now be excluded at search time, not merely scored low
    # later.
    "Chief Information Security Officer",  # real title
    "Chief Information Security Officer, NB-2210-VIII",  # real title
    "Group Chief Information Security Officer",  # real title
    "SVP, Chief Information Security Officer",  # real title
    "VP, Infrastructure & Chief Information Security Officer",  # real title
    "Chief Information Security Officer (CISO) - AI Trainer",  # real title
    "VP/CISO, Information Security",  # real title - bare CISO abbreviation
])
def test_information_security_layer_now_excludes_ciso_titles(title):
    result = exclusion_filter.check_exclusion(_job(title), custom_exclusions=[])
    assert result is not None
    assert result["rule"] == "information_security_domain"


def test_information_security_layer_does_not_match_unrelated_titles():
    # "information" alone (with no "security") should not trip this layer -
    # only the standalone word "security" does, since 2026-08-13's
    # broadening. "Director, IT Service Continuity" and "VP Information
    # Technology" contain neither.
    assert exclusion_filter.check_exclusion(_job("Director, IT Service Continuity"), custom_exclusions=[]) is None
    assert exclusion_filter.check_exclusion(_job("VP Information Technology"), custom_exclusions=[]) is None


def test_information_security_layer_now_excludes_any_security_title():
    # Broadened 2026-08-13: unlike the old narrow "information security"
    # phrase match, a plain "security" title (no "information") is now
    # also excluded.
    result = exclusion_filter.check_exclusion(_job("Director of Security Operations"), custom_exclusions=[])
    assert result is not None
    assert result["rule"] == "information_security_domain"


def test_information_security_layer_fires_even_when_seniority_would_have_kept_it():
    # "Director of Information Security" carries "Director", which would
    # satisfy the seniority layer's exec-qualifier check on its own - layer
    # 8 must still exclude it independently, same shape as layer 2's
    # "Medical Director" regression guard.
    result = exclusion_filter.check_exclusion(_job("Director of Information Security (Hybrid)"), custom_exclusions=[])
    assert result["rule"] == "information_security_domain"


# --- Logging: the non-negotiable "never silently dropped" requirement ------

def test_log_exclusions_appends_full_record(isolated_data):
    job = _job("Registered Nurse", job_id="42", organization="Big Health System", location="Dallas, TX")
    exclusion = {"rule": "clinical_domain", "reason": "clinical/medical domain role (matched \"registered nurse\")"}
    exclusion_filter.log_exclusions([(job, exclusion)])

    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "Dice"
    assert entry["job_id"] == "42"
    assert entry["title"] == "Registered Nurse"
    assert entry["organization"] == "Big Health System"
    assert entry["location"] == "Dallas, TX"
    assert "clinical_domain" in entry["exclusion_reason"]
    assert "timestamp" in entry


def test_log_exclusions_is_a_no_op_on_empty_list(isolated_data):
    exclusion_filter.log_exclusions([])
    assert not exclusion_filter.EXCLUSION_LOG_PATH.exists()


def test_log_exclusions_dedupes_against_already_logged_source_job_id(isolated_data):
    job = _job("Registered Nurse", job_id="1")
    exclusion = {"rule": "clinical_domain", "reason": "x"}
    exclusion_filter.log_exclusions([(job, exclusion)])
    # Same job resurfacing in a later search run (still-open posting) must
    # not create a second log entry - see log_exclusions()'s own docstring
    # on unbounded growth.
    exclusion_filter.log_exclusions([(job, exclusion)])

    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    assert len(entries) == 1


def test_log_exclusions_accumulates_distinct_jobs(isolated_data):
    exclusion_filter.log_exclusions([
        (_job("Registered Nurse", job_id="1"), {"rule": "clinical_domain", "reason": "a"}),
        (_job("Sr. Systems Engineer", job_id="2"), {"rule": "seniority_mismatch", "reason": "b"}),
    ])
    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    assert len(entries) == 2
    assert [e["job_id"] for e in entries] == ["1", "2"]


# --- list_exclusions(): 30-day default view, full-history "show all" -------

def _seed_entry(timestamp_iso, job_id="1"):
    """Writes one exclusion-log entry directly (bypassing log_exclusions()'s
    "now" timestamp) so tests can control exactly how old an entry is."""
    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    entries.append({
        "timestamp": timestamp_iso,
        "source": "Dice",
        "job_id": job_id,
        "title": "Registered Nurse",
        "organization": "Big Health System",
        "location": "Dallas, TX",
        "exclusion_reason": "clinical_domain: x",
    })
    write_json(exclusion_filter.EXCLUSION_LOG_PATH, entries)


def test_list_exclusions_default_excludes_entries_older_than_30_days(isolated_data):
    now = datetime.now(timezone.utc)
    _seed_entry((now - timedelta(days=45)).isoformat(), job_id="old")
    _seed_entry((now - timedelta(days=5)).isoformat(), job_id="recent")

    result = exclusion_filter.list_exclusions()

    assert [e["job_id"] for e in result] == ["recent"]


def test_list_exclusions_days_back_none_returns_full_history(isolated_data):
    now = datetime.now(timezone.utc)
    _seed_entry((now - timedelta(days=45)).isoformat(), job_id="old")
    _seed_entry((now - timedelta(days=5)).isoformat(), job_id="recent")

    result = exclusion_filter.list_exclusions(days_back=None)

    assert {e["job_id"] for e in result} == {"old", "recent"}


def test_list_exclusions_custom_days_back(isolated_data):
    now = datetime.now(timezone.utc)
    _seed_entry((now - timedelta(days=10)).isoformat(), job_id="within_7")
    _seed_entry((now - timedelta(days=3)).isoformat(), job_id="within_7_too")

    result = exclusion_filter.list_exclusions(days_back=7)

    assert [e["job_id"] for e in result] == ["within_7_too"]


def test_list_exclusions_on_empty_log_returns_empty_list(isolated_data):
    assert exclusion_filter.list_exclusions() == []
    assert exclusion_filter.list_exclusions(days_back=None) == []


def test_list_exclusions_boundary_entry_just_inside_30_days_is_included(isolated_data):
    # Just inside the 30-day window (29d 23h ago, not exactly "days=30" to
    # avoid a flaky race against list_exclusions() computing its own "now"
    # a moment after this seed does) - confirms no off-by-one excludes an
    # entry that should still be in the default view.
    now = datetime.now(timezone.utc)
    _seed_entry((now - timedelta(days=29, hours=23)).isoformat(), job_id="boundary")

    result = exclusion_filter.list_exclusions(days_back=30)

    assert [e["job_id"] for e in result] == ["boundary"]
