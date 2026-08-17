"""Search-time exclusion filter that stops predictably-poor-fit jobs from
ever entering data/jobs/jobs.json in the first place, so they never reach
tailoring.fit_score's paid Opus call at all (real analysis, 2026-08-12,
following the same week's tailoring.fit_score_prefilter build: a large
fraction of jobs that fit_score scores near-zero are knowable from the
TITLE ALONE, before spending a single paid call - 27/28 real AbbVie jobs
scored <=60 followed the seniority pattern below, and clinical-domain
titles score 0 with an unambiguous rationale every time).

Unlike tailoring.fit_score_prefilter (which runs just before a scoring
call, on jobs already sitting in the store), this runs on EVERY job coming
out of EVERY search channel (USAJOBS, ZipRecruiter, Dice, Indeed, company
sites, industry boards) before search.job_store.save_jobs() ever writes a
record - so it must stay purely deterministic (no AI call, no network
call) to be cheap enough to run at that volume. Four layers:

1. Seniority-tier exclusion: the candidate (Zahir) is a 25-year VP/CIO/
   Head-of-IT executive. An individual-contributor-tier noun in the title
   (Engineer, Analyst, Specialist, Consultant, Scientist, Representative,
   Coordinator, Associate) almost always means a near-zero fit_score
   regardless of domain match - UNLESS the title is also Director-level or
   above (Director, VP, Vice President, Head, Chief, President, SVP,
   EVP), which qualifies it back in. "Senior Systems Engineer" excludes
   (Engineer, no qualifier); "Senior Director" and "Associate Director"
   both keep (Director qualifies "Associate" the same way it qualifies
   "Senior").
2. Clinical/medical domain exclusion: Medical Director, Physician, Nurse
   Practitioner, Registered Nurse, Clinical Research/Development/
   Scientist/Pharmacology, Medical Science Liaison, Medical Advisor,
   Laboratory/Lab Technician - verified: "Senior Medical Director,
   Hematology Clinical Development" at AbbVie scored 0 four separate times
   with an identical "clinical role, no domain overlap" rationale. This
   layer is deliberately independent of layer 1 - "Medical Director"
   carries an executive-qualifying word ("Director") that would otherwise
   keep it, so the clinical check must run regardless of the seniority
   verdict, not only when seniority already excluded it. Extended
   2026-08-17 with lab/technician phrase matching ("laboratory
   technician", "lab technician", "lab tech") after two real Beacon Hill
   Life Sciences (industry board) jobs - "Veterinary Laboratory
   Technician" and "Quality Control Laboratory Technician" - slipped
   through: neither carried an IC-tier noun from layer 1 ("Technician"
   isn't in that list) nor matched any prior clinical phrase. See the
   pattern definition below for the false-positive check against real
   "Lab Compute Analyst" (AbbVie) and "IT/Cloud Technician" (USAJOBS/U.S.
   Courts) titles already in the live store.
3. Custom title exclusions (added 2026-08-13): the user's own free-text
   terms from the Settings tab, stored in config/settings.yaml's
   "custom_title_exclusions" key (see load_custom_title_exclusions() and
   _custom_exclude() below) - plain case-insensitive substring matching,
   not the \b-boundary regex the built-in layers use, since a
   non-technical user typing a free-form fragment expects "contains this
   text" behavior.
4. Generic administrative/clerical/demo-support role exclusion (added
   2026-08-17, Zahir's explicit request after "Value Proposition and
   Demonstration Manager" and "Senior Administrative Assistant" - two real
   AbbVie company-site jobs, neither remotely IT/cybersecurity/digital-
   transformation - slipped through to him unfiltered). Matches titled
   roles like "Administrative Assistant," "Executive Assistant," "Office
   Manager," "Receptionist," and "Demonstration Manager"/"Value
   Proposition Manager" - none of these carry an IC-tier noun from layer
   1's pattern (so layer 1 never catches them: "Assistant"/"Manager"
   aren't in that list), and they're not a clinical/medical role either,
   so layer 2 doesn't catch them. Deliberately titled-phrase matching, not
   a generic "admin" substring - "Administrator" (a real, distinct
   technical title: "Systems Administrator," "Database Administrator,"
   "Network Administrator," "Operational Technology Systems
   Administrator," all real titles in the live store) shares no common
   substring with "Administrative Assistant" once matched as whole words,
   so there is no risk of conflating the two. Also exempts any title that
   independently carries an IT/technical qualifier word (IT, systems,
   technology, technical, digital, network, infrastructure, security,
   software, data, cloud, cyber, informatics) anywhere in the title - a
   hedge against a real but
   not-yet-seen title like "IT Office Manager" or "Digital Demonstration
   Manager" that would otherwise be a false positive; validated 2026-08-17
   against the full live job store (2 real matches: "Senior Administrative
   Assistant" and "Senior Administrative Assistant, IMCO, Eyecare &
   Specialty," both genuinely non-technical - zero false positives against
   every real "Administrator"/"CIO"/"Director"/"VP" title in the store).
5. Hands-on IC engineering/architecture title exclusion (added 2026-08-17,
   Zahir's explicit request after four real jobs - "SVP, Full-Stack Engr"/
   "VP, Full-Stack Engr II" (Bank of New York Mellon, via Dice), "Senior
   Full Stack Engineer, ATM Platforms - VP" (Citi, via SimplyHired), "SVP
   Lead Full Stack Engineer" (Citi) - none of which match his IT-
   leadership/CIO-track profile despite senior-sounding titles. In
   banking, "VP"/"SVP" is very often a PAY-GRADE prefix, not an
   org-leadership title - these are genuinely hands-on individual-
   contributor engineering roles. Matches "full-stack engineer"/"full
   stack engineer" (including the "Full-Stack Engr"/"Engr II" abbreviation
   BNY Mellon's Dice postings actually use), "software engineer",
   "backend engineer"/"back-end engineer", "frontend engineer"/"front-end
   engineer", "platform engineer", and "atm architect" - as the literal
   role noun, regardless of any VP/SVP/Senior/Principal/Lead prefix.
   Deliberately anchored on "engineer"/"architect" as the role noun with a
   trailing \b, not "engineering"/"architecture" - this alone is what
   keeps a real leadership title like "VP of Engineering", "Director of
   Software Engineering", or "Head of Platform Engineering" out: none of
   them contain the bare noun "engineer"/"architect" followed by a word
   boundary ("engineering" fails the \b check after "engineer" since "ing"
   continues the word). Also exempts any title containing "manager" as a
   whole word (e.g. "Software Engineering Manager", "Engineering
   Manager") - genuine people-management roles Zahir still wants surfaced,
   not hands-on IC work, even though the "engineering" vs. "engineer"
   distinction above already excludes the two concrete examples found in
   the live store. Validated 2026-08-17 against the full live+archive job
   store (see check_exclusion() call site history): 9 real live-store
   matches (all Dice/SimplyHired/Built In banking postings with VP/SVP
   prefixes, e.g. the four titles above plus "SVP Senior KDB+ Platform
   Engineer", "SVP, Principal Full Stack Engineer - Performance Product
   Engineering", "ATM Architect"), zero false positives against every
   real "VP of Engineering"/"Director of Software Engineering"/"Head of
   Platform Engineering"/"CIO"/"CTO"/"Engineering Manager"-shaped title in
   the store - none of those 100+ leadership titles contain the bare
   "engineer"/"architect" noun this layer keys on.

Branch experiment/filter-quality-improvements (started 2026-08-17, off
master's tip after the seven layers above, per Zahir's explicit "10% noise
is acceptable, keep improving it but hold this round for my review before
merging" instruction) measured the then-current 599-job pending-review
queue against the full live store and found roughly 95 real jobs (~16%)
a reasonable IT-leadership-focused reviewer would call clear noise - none
of them caught by any of the seven layers above. The three highest-
frequency real remaining patterns got built here as three more layers:

6. Extended layer 1's IC-tier noun list with "administrator", "technician",
   and the government "spec"/"itspec" IT-specialist abbreviation (22 real
   pending matches - mostly USAJOBS/Air National Guard/U.S. Courts
   non-executive IT support titles).
7. Extended layer 2's clinical/medical pattern with the pharma "CMC"
   (Chemistry, Manufacturing & Controls) abbreviation and broader
   clinical-operations phrases (23 real pending AbbVie/GForce Life
   Sciences matches).
8. Extended layer 5's hands-on-IC engineering pattern with the bare
   "developer" role noun, following the same banking VP/SVP-pay-grade
   rationale as the existing engineer/architect nouns (2 real Citi
   "...Developer, Vice President"-shaped matches, plus 7 non-prefixed
   USAJOBS "...Developer" matches layer 1 already excluded on its own).
9. A new layer 6 (see _NON_IT_COMMERCIAL_PATTERN below): non-IT
   sales/finance/wealth-management/credit/card commercial-leadership
   titles, mostly JPMorganChase/Citi/Morningstar/Jefferies banking
   postings (35 real pending matches) - none clinical, admin, or
   hands-on-IC, just the wrong domain, the same class of gap layer 2
   already exists to close for medical roles.

See docs on each pattern below for the full validation detail. This
branch is exploratory/accumulating and is NOT merged to master - held for
Zahir's explicit review per his standing instruction, same as every
change in this repo (see docs/release-manager.md), but deliberately not
routed through that process yet.

Non-negotiable per Zahir's standing "never silently dropped" rule (the
same one tailoring.fit_score_prefilter follows): an excluded job is never
just gone. Every exclusion is appended to
data/jobs/search_exclusion_log.json (same locked-write pattern as
prefilter_log.json) BEFORE job_store.save_jobs() ever gets a chance to
write the job into jobs.json - the log is the only record these jobs ever
existed, so it must be written unconditionally, not best-effort.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from security.crypto_store import read_json, write_json
from security.file_lock import locked

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXCLUSION_LOG_PATH = PROJECT_ROOT / "data" / "jobs" / "search_exclusion_log.json"

# Layer 3 (2026-08-13, Settings tab "Custom title exclusions" build): the
# user's own free-text list, stored in config/settings.yaml alongside
# target_roles/industries/etc. (same plain-YAML store ui/app.py's
# load_settings()/save_settings() already read/write - no new storage
# layer for this). Read here directly rather than importing
# ui.app.load_settings() to avoid a search -> ui import (ui already
# imports from search; the reverse would be circular).
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

# Layer 1: seniority-tier exclusion. \b word boundaries throughout so e.g.
# "head" never matches inside "headquarters"/"overhead" and "chief" never
# matches inside an unrelated word - both real risks with naive substring
# matching.
#
# Extended 2026-08-17 (experiment/filter-quality-improvements, measuring the
# post-seven-layer pending-review baseline against Zahir's 10%-noise
# tolerance target) with "administrator", "technician", and the government
# "IT SPEC" abbreviation ("spec"/"itspec", both spaced and unspaced forms
# appear in real USAJOBS/Air National Guard postings) - none of the
# original eight nouns cover these, so a bare non-executive support/
# administration title (22 real live-pending examples: "IT Support
# Technician I"/"IT Technician II" at U.S. Courts, "Cloud/Infrastructure
# Technician"/"Operating Systems Technician" at Social Security
# Administration, "IT SPEC (INFOSEC)"/"ITSPEC (NETWORK)" at Air National
# Guard Units, "Operational Technology Systems Administrator" via Dice)
# reached the pending queue unfiltered.
#
# Two real, deliberate behavior changes this introduces (not bugs - see
# the corresponding regression tests below for the full reasoning):
# (1) A bare "Systems Administrator"/"Database Administrator"/"Network
# Administrator" with NO seniority prefix at all - previously silently
# kept on master, since "administrator" was never in this noun list - is
# now excluded, closing an inconsistency where an equally IC-tier bare
# "Systems Engineer"/"Business Analyst"/"IT Specialist" was already
# excluded. Validated against the full live store: only 3 real bare-
# "Administrator" titles with no exec qualifier exist at all, all
# genuinely non-leadership IC roles - zero real leadership-relevant
# titles are affected. (2) "FVP/SVP, Credit Administrator" (real Cathay
# Bank job) still carries the "SVP" exec qualifier so layer 1 itself
# still exempts it exactly as before - it's layer 6 below (new this
# branch) that now separately excludes it, on domain grounds ("Credit"),
# not a layer 1 change.
_IC_TIER_PATTERN = re.compile(
    r"\b(engineer|analyst|specialist|consultant|scientist|representative|coordinator|associate"
    r"|administrator|technician|spec)\b"
    r"|\bitspec\b",
    re.I,
)
_EXEC_QUALIFIER_PATTERN = re.compile(
    r"\b(director|vice president|vp|head|chief|president|svp|evp)\b",
    re.I,
)

# Layer 2: clinical/medical domain exclusion. Independent of layer 1 -
# "Medical Director" must exclude here even though "Director" would
# otherwise qualify it past the seniority check.
_CLINICAL_PATTERN = re.compile(
    r"\bmedical director\b"
    r"|\bphysician\b"
    r"|\bnurse practitioner\b"
    r"|\bregistered nurse\b"
    r"|\bclinical research\b"
    r"|\bclinical development\b"
    r"|\bclinical scientist\b"
    r"|\bclinical pharmacology\b"
    r"|\bmedical science liaison\b"
    r"|\bmedical advisor\b"
    # Added 2026-08-17: two real Beacon Hill Life Sciences (industry
    # staffing board) jobs - "Veterinary Laboratory Technician" and
    # "Quality Control Laboratory Technician" - slipped through unfiltered.
    # Neither carries an IC-tier noun from layer 1's pattern ("Technician"
    # isn't in that list) and neither matched any existing clinical phrase
    # above, so both reached Zahir unfiltered. Deliberately phrase-matching
    # "lab/laboratory technician", not a bare "\blab\b" or "\btechnician\b"
    # substring: validated against the full live job store (2026-08-17) -
    # "Lab Compute Analyst"/"Lab Compute Senior Analyst" (AbbVie, real IT
    # roles managing lab computing systems), "Cloud/Infrastructure
    # Technician", "IT Support Technician I", "IT Technician II" (all real
    # USAJOBS/U.S. Courts IT roles) all contain "lab" or "technician" in
    # isolation but never the contiguous "lab/laboratory technician"
    # phrase, so a narrower substring would have false-positived on real
    # IT-relevant titles where this phrase-match does not.
    r"|\blaboratory technician\b"
    r"|\blab technician\b"
    r"|\blab tech\b"
    # Added 2026-08-17 (experiment/filter-quality-improvements): the
    # single largest real remaining noise cluster found while measuring the
    # pending-queue baseline - 23 real pending AbbVie/GForce Life Sciences
    # jobs in two related pharma-domain shapes neither existing clinical
    # phrase nor any other layer catches. (a) "CMC" (Chemistry,
    # Manufacturing & Controls - pharma regulatory-affairs/technical-
    # development function): 13 real "Associate Director, RA CMC"/"Director
    # of CMC, External Operations"/"Scientific Technical Lead ... CMC"
    # titles at AbbVie. Matched as the bare abbreviation \bcmc\b -
    # deliberately no tech-qualifier exemption here (unlike layer 4/6
    # below): "Scientific Technical Lead, Early Stage PDST CMC" contains
    # the word "Technical", which would have been wrongly exempted by a
    # generic IT-tech-qualifier check - "Technical" here means pharma R&D
    # technical work, not IT. Validated against the full live+archive
    # store: every real title containing "CMC" (16 total, live + one
    # not-yet-reviewed) is a pharma regulatory/technical role, zero IT
    # titles collide with the abbreviation. (b) Non-CMC pharma
    # clinical-operations/services titles that don't match any existing
    # phrase above (none of them are "clinical research/development/
    # scientist/pharmacology" or a medical-science/advisor role): "Clinical
    # Site Lead" (6 real duplicate GForce Life Sciences staffing postings),
    # "Clinical Operations" (Associate Director/Director-tier AbbVie
    # titles), "Clinical Process Excellence" (AbbVie), "Clinical Services"/
    # "Clinical Psychologist" (Project Vida). None of these carry a tech
    # qualifier either, so no exemption is needed or added.
    r"|\bcmc\b"
    r"|\bclinical site lead\b"
    r"|\bclinical operations\b"
    r"|\bclinical process excellence\b"
    r"|\bclinical services\b"
    r"|\bclinical psychologist\b",
    re.I,
)

# Layer 4: generic administrative/clerical/demo-support role exclusion.
# Titled-phrase matching only (never a bare "admin" substring) so a real
# technical "Administrator" title (Systems/Database/Network Administrator)
# can never be conflated with "Administrative Assistant" - the two share no
# common substring once matched as whole titled phrases.
_ADMIN_SUPPORT_PATTERN = re.compile(
    r"\badministrative assistant\b"
    r"|\bexecutive assistant\b"
    r"|\boffice manager\b"
    r"|\breceptionist\b"
    r"|\bfront desk\b"
    r"|\bvalue proposition\b"
    r"|\bdemonstration manager\b",
    re.I,
)

# Exemption: any of these words appearing anywhere else in the title signals
# a real IT/technical role, even one titled with an otherwise-generic
# administrative/support phrase (e.g. a not-yet-seen "IT Office Manager" or
# "Digital Demonstration Manager") - a real technical title must never be
# caught by this layer.
_TECH_QUALIFIER_PATTERN = re.compile(
    r"\b(it|information technology|systems|technology|technical|digital|network|"
    r"infrastructure|security|software|data|cloud|cyber|informatics)\b",
    re.I,
)

# Layer 5: hands-on IC engineering/architecture title exclusion. Anchored on
# the bare role noun "engineer"/"architect" with a trailing \b - this is
# deliberately what keeps "...Engineering"/"...Architecture" leadership
# titles (VP of Engineering, Director of Software Engineering, Head of
# Platform Engineering, Head of Enterprise Architecture) out without any
# separate exemption list: \bengineer\b never matches inside "engineering"
# (the "ing" continues the word, failing the trailing boundary), same for
# \barchitect\b inside "architecture". "Full-Stack Engr"/"Full-Stack Engr II"
# is BNY Mellon's own Dice-posting abbreviation for "Full-Stack Engineer" -
# matched explicitly since \bengineer\b alone would miss it.
#
# Extended 2026-08-17 (experiment/filter-quality-improvements) with the bare
# "\bdeveloper\b" role noun, following this layer's own established
# rationale rather than layer 1's: two real Citi titles - "Senior Java
# Developer, FX eCommerce, Vice President" and "Lead Java Developer -
# Senior Vice President" - carry the same banking VP/SVP-as-pay-grade
# pattern this layer already exists to catch for "engineer"/"architect",
# just with "Developer" as the hands-on IC role noun instead. Adding
# "developer" here (governed only by the _PEOPLE_MANAGEMENT_PATTERN
# exemption below, same as every other noun in this layer - no VP/SVP
# exemption) rather than to layer 1's _IC_TIER_PATTERN (which DOES exempt
# on VP/SVP/Director) was a deliberate choice: layer 1 would have kept
# both real Citi titles since "Vice President"/"Senior Vice President" is
# an exec-qualifying phrase there, reproducing the exact gap layer 5 was
# built to close for "engineer" titles. This also naturally catches seven
# real non-executive USAJOBS Social Security Administration titles with no
# prefix at all ("COBOL Developer", "Python Developer", "PEGA Developer-
# DHA", "Business Intelligence Platform Developer" x2, two "...Full Stack
# Web Application Developer" variants) that layer 1 already excludes on
# its own merits (no exec qualifier present) - this addition doesn't change
# their outcome, only ensures the two VP/SVP-prefixed banking titles above
# are excluded too. Validated against the full live+archive store: the one
# other real title carrying both "Developer" and an exec-qualifying word -
# "SVP, Senior Oracle AML Manager/Developer" (Jefferies) - also carries
# "Manager", so the existing _PEOPLE_MANAGEMENT_PATTERN exemption below
# correctly keeps it (genuine dual-titled management role), same as it
# already does for "Software Engineering Manager"-shaped titles.
_IC_ENGINEER_ROLE_PATTERN = re.compile(
    r"\bfull[- ]stack\s+engr\.?\b"
    r"|\bfull[- ]stack\s+engineer\b"
    r"|\bsoftware engineer\b"
    r"|\bback[- ]?end engineer\b"
    r"|\bfront[- ]?end engineer\b"
    r"|\bplatform engineer\b"
    r"|\batm architect\b"
    r"|\bdeveloper\b",
    re.I,
)

# Exemption: "Manager" appearing anywhere in the title signals genuine
# people-management (e.g. "Software Engineering Manager", "Engineering
# Manager") - Zahir still wants these surfaced, they are not hands-on IC
# work. Belt-and-suspenders alongside the engineer/engineering \b
# distinction above, which already excludes every concrete "Manager" title
# found in the live store on its own.
_PEOPLE_MANAGEMENT_PATTERN = re.compile(r"\bmanager\b", re.I)

# Layer 6 (added 2026-08-17, experiment/filter-quality-improvements): non-IT
# commercial/finance leadership exclusion. The second-largest real remaining
# noise cluster found while measuring the pending-queue baseline - 35 real
# pending jobs, mostly JPMorganChase/Citi/Morningstar/Jefferies banking
# postings, titled around a pure sales, finance, credit-risk, wealth-
# management, or card/payments-product role with no IT/technology content
# anywhere in the title. None of the prior six layers catch these: they
# carry genuine exec-qualifying words (VP/SVP/Director/Chief), aren't
# clinical/medical, aren't a generic admin/clerical phrase, and aren't a
# hands-on IC engineering title - they're simply the wrong domain, the same
# class of gap layer 2 (clinical) already exists to close for medical
# roles, just for commercial/banking domains instead.
#
# Phrase/noun list: "sales" and "finance" as bare role nouns (deliberately
# NOT "financial" - "Chief Financial Officer"/"VP Financial Planning" stay
# untouched, out of scope for this pass), "credit" (credit-risk/ratings/
# portfolio roles), "wealth management"/"private wealth" (JPMorgan's Wealth
# Management line of business), and "card" (payments/card-product roles -
# deliberately bare since "Card" never appears as a false-positive
# substring of any real IT title in the store, e.g. no "Smart Card
# Infrastructure Engineer"-shaped title exists today; revisit if one ever
# does).
_NON_IT_COMMERCIAL_PATTERN = re.compile(
    r"\bsales\b"
    r"|\bfinance\b"
    r"|\bcredit\b"
    r"|\bwealth management\b"
    r"|\bprivate wealth\b"
    r"|\bcard\b",
    re.I,
)

# Exemption: same IT/technical-qualifier signal as layer 4's
# _TECH_QUALIFIER_PATTERN, extended with "cio"/"cto"/"ciso" and their
# spelled-out forms - real titles like "Vice President, Finance - CIO North
# America" (JPMorganChase, a genuine Finance-division CIO role) and "Sr.
# Director, IT Product Management - Post Sales and Partner Technology"
# (MongoDB) must stay kept. Deliberately a separate pattern from layer 4's
# rather than reusing it as-is: "cio"/"cto"/"ciso" collide with finance's
# own "Chief Investment Officer" abbreviation (e.g. "Wealth Management,
# Quantitative Portfolio Manager, Equities CIO" is really a Chief
# Investment Officer title, not IT) - validated this is the safe direction
# for that collision, since exempting a genuinely non-IT title here only
# risks under-excluding it (it still reaches Zahir for him to reject
# himself), never wrongly excluding a real IT-leadership title, which is
# the one outcome this layer must never produce.
_COMMERCIAL_TECH_QUALIFIER_PATTERN = re.compile(
    r"\b(it|information technology|systems|technology|technical|digital|network|"
    r"infrastructure|security|software|data|cloud|cyber|informatics|"
    r"cio|cto|ciso)\b",
    re.I,
)


def _seniority_exclude(title: str) -> str | None:
    if _IC_TIER_PATTERN.search(title) and not _EXEC_QUALIFIER_PATTERN.search(title):
        return "individual-contributor-tier title with no executive-qualifying word present"
    return None


def _clinical_exclude(title: str) -> str | None:
    match = _CLINICAL_PATTERN.search(title)
    if match:
        return f"clinical/medical domain role (matched \"{match.group(0)}\")"
    return None


def load_custom_title_exclusions() -> list[str]:
    """Returns the user's own free-text exclusion terms from
    config/settings.yaml's "custom_title_exclusions" key - already
    split/trimmed at save time (see ui/app.py's Settings tab handler), so
    this returns them as-is. Missing file or missing key both resolve to
    an empty list, not an error - a fresh install/an unused field is the
    common case and must be a true no-op, not a crash or a spurious
    exclusion.

    Called once per save_jobs() batch (not once per job) by
    job_store.save_jobs(), which passes the result into check_exclusion()
    for every job in that batch - avoids re-reading this file once per
    job on what can be a large multi-source search result."""
    if not SETTINGS_PATH.exists():
        return []
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("custom_title_exclusions") or []


def _custom_exclude(title: str, custom_exclusions: list[str]) -> str | None:
    """Case-insensitive substring match, deliberately not the \\b
    word-boundary regex the built-in layers use above: a non-technical
    user typing a free-form fragment (e.g. "Program Director" meant to
    catch "Senior Program Director, Clinical Ops") expects plain "contains
    this text" behavior, not regex semantics they never opted into."""
    title_lower = title.lower()
    for term in custom_exclusions:
        term_clean = (term or "").strip()
        if term_clean and term_clean.lower() in title_lower:
            return f"matched custom excluded term \"{term_clean}\""
    return None


def _admin_support_exclude(title: str) -> str | None:
    match = _ADMIN_SUPPORT_PATTERN.search(title)
    if not match:
        return None
    if _TECH_QUALIFIER_PATTERN.search(title):
        return None
    return f"generic non-technical administrative/clerical/demo-support role (matched \"{match.group(0)}\")"


def _ic_engineer_exclude(title: str) -> str | None:
    match = _IC_ENGINEER_ROLE_PATTERN.search(title)
    if not match:
        return None
    if _PEOPLE_MANAGEMENT_PATTERN.search(title):
        return None
    return (
        "hands-on IC engineering/architecture title - VP/SVP prefix is a banking "
        f"pay-grade, not org leadership (matched \"{match.group(0)}\")"
    )


def _non_it_commercial_exclude(title: str) -> str | None:
    match = _NON_IT_COMMERCIAL_PATTERN.search(title)
    if not match:
        return None
    if _COMMERCIAL_TECH_QUALIFIER_PATTERN.search(title):
        return None
    return f"non-IT sales/finance/wealth/credit commercial role (matched \"{match.group(0)}\")"


def check_exclusion(job: dict, custom_exclusions: list[str] | None = None) -> dict | None:
    """Returns {"rule": ..., "reason": ...} if this job should never be
    persisted, or None if it should go through job_store.save_jobs()'s
    normal path. All six layers are checked independently (not
    short-circuit on an earlier layer's verdict) - see this module's own
    docstring on why "Medical Director" needs layer 2 to fire regardless
    of layer 1; layers 3 (the user's own custom terms), 4 (generic
    administrative/support titles), 5 (hands-on IC engineering/
    architecture titles), and 6 (non-IT commercial/finance leadership
    titles) are likewise checked even when earlier layers already passed,
    so any of them can catch a title the others wouldn't.

    custom_exclusions=None (the default) makes this call
    load_custom_title_exclusions() itself, for any caller that doesn't
    already have the list on hand (e.g. a one-off/test call). Real
    per-job callers in a loop (job_store.save_jobs()) should load once and
    pass the same list to every check_exclusion() call instead, to avoid
    re-reading settings.yaml once per job."""
    title = job.get("title") or ""

    reason = _seniority_exclude(title)
    if reason:
        return {"rule": "seniority_mismatch", "reason": reason}

    reason = _clinical_exclude(title)
    if reason:
        return {"rule": "clinical_domain", "reason": reason}

    if custom_exclusions is None:
        custom_exclusions = load_custom_title_exclusions()
    reason = _custom_exclude(title, custom_exclusions)
    if reason:
        return {"rule": "custom_user_exclusion", "reason": reason}

    reason = _admin_support_exclude(title)
    if reason:
        return {"rule": "administrative_support_role", "reason": reason}

    reason = _ic_engineer_exclude(title)
    if reason:
        return {"rule": "ic_engineer_title", "reason": reason}

    reason = _non_it_commercial_exclude(title)
    if reason:
        return {"rule": "non_it_commercial_role", "reason": reason}

    return None


def _log_entry(job: dict, exclusion: dict) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": job.get("source"),
        "job_id": job.get("job_id"),
        "title": job.get("title"),
        "organization": job.get("organization"),
        "location": job.get("location"),
        "exclusion_reason": f"{exclusion['rule']}: {exclusion['reason']}",
    }


def log_exclusions(entries: list[tuple[dict, dict]]) -> None:
    """Batch-appends exclusion records: entries is a list of (job,
    exclusion) pairs, matching check_exclusion()'s return shape. A no-op on
    an empty list (avoids an unnecessary locked read/write on every
    save_jobs() call that excludes nothing - the common case).

    De-dupes against records already logged for the same (source, job_id),
    same principle as job_store.save_jobs()'s own dedup: a job that never
    enters jobs.json has no "seen" set to protect it from reappearing in
    tomorrow's search results for the same still-open posting, so without
    this the log would grow by one duplicate entry per excluded job per
    day, forever, for as long as a posting stays live and keeps surfacing
    in search results - the exact unbounded-growth pattern CLAUDE.md's
    performance principle warns against."""
    if not entries:
        return
    with locked("search_exclusion_log"):
        existing = read_json(EXCLUSION_LOG_PATH, default=[])
        seen = {(e.get("source"), e.get("job_id")) for e in existing}
        changed = False
        for job, exclusion in entries:
            key = (job.get("source"), job.get("job_id"))
            if key in seen:
                continue
            existing.append(_log_entry(job, exclusion))
            seen.add(key)
            changed = True
        if changed:
            write_json(EXCLUSION_LOG_PATH, existing)


def list_exclusions(days_back: int | None = 30) -> list[dict]:
    """Read-only query over the full exclusion log (never prunes/deletes -
    log_exclusions() above is the only writer, and it never removes an
    entry either, so full history always stays on disk).

    days_back=30 (the default) returns only entries logged in the last 30
    days - the default view a caller (e.g. a future Settings-tab toggle)
    should show without the user having to ask for "everything ever
    excluded". days_back=None returns the full, unfiltered log - the real
    "show all" backing this default is meant to toggle to; there's no UI
    for that toggle yet (not this build's job), but the function it will
    call needs to exist and be correct now.

    Malformed/missing "timestamp" entries (there shouldn't be any, since
    _log_entry() always stamps one, but this is read-only history that
    could in principle predate this function) are treated as always-
    outside-the-window rather than raising or being silently included -
    conservative default for a query whose whole point is "don't show me
    stale noise by default"."""
    entries = read_json(EXCLUSION_LOG_PATH, default=[])
    if days_back is None:
        return entries

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    result = []
    for entry in entries:
        timestamp = entry.get("timestamp")
        if not timestamp:
            continue
        try:
            logged_at = datetime.fromisoformat(timestamp)
        except ValueError:
            continue
        if logged_at.tzinfo is None:
            logged_at = logged_at.replace(tzinfo=timezone.utc)
        if logged_at >= cutoff:
            result.append(entry)
    return result
