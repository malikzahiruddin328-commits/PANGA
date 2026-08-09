"""Deterministic post-generation fact-checking for a drafted resume
(2026-08-09, Zahir's explicit design, confirmed and scoped with General) -
NOT a second AI call. The existing "?" hedge gate (tailoring.
unconfirmed_claims) only ever catches what the AI ITSELF flagged as
uncertain; nothing verified an UNHEDGED claim was actually real, so a
confidently-stated fabrication (wrong employer, wrong dates, an invented
certification) sailed straight through every gate untouched.

Deliberately scoped to the mechanically-checkable, highest-confidence
surfaces only, per the same "no fuzzy guessing where it can't be
confident" restraint as ats_score.detect_keyword_wording_regressions -
false positives here would be exhausting for Zahir to review and would
erode trust in the check fast (General's explicit warning, backed by a
real example: "$1B" vs "1 billion dollars" or a legitimately-calculated
percentage must never get flagged as fabricated):

- Resume role-header lines, but ONLY inside the PROFESSIONAL EXPERIENCE
  section (2026-08-09, RM's real finding: EDUCATION lines like "Brunel
  University London, Middlesex, UK, 1997 - 2001" also match the same
  date-range pattern and were wrongly checked against work_history before
  this was section-gated - flagged 23/28 of Zahir's real resumes). Within
  that section: the employer text must trace back (skills_match) to a
  real profile.work_history entry, and the drafted date range's YEARS
  must fall within that entry's real span - or, for a sub-role line with
  NO employer text of its own on the same line (see "employer context"
  below), within the combined span of every real entry sharing the most
  recently-named real employer. Year-granularity only, not month-precise
  - the drafted text renders "Month YYYY" while the profile stores
  "MM/YYYY"/"YYYY", and month-level reconciliation between two different
  formats is exactly the kind of precision this module has no business
  claiming.
- Certification lines (inside a CERTIFICATIONS section) must trace back
  to a real profile.certifications entry - either the whole phrase
  (skills_match) or a shared all-caps acronym token (2026-08-09, RM's
  real finding: "Reltio MDM Certification" vs drafted "Reltio Master
  Data Management (MDM) Certification" - a real, legitimate expansion of
  the same acronym, not a fabrication - failed skills_match's literal
  phrase containment). Known gap: Zahir's real CERTIFICATIONS section
  bundles all of them onto ONE " | "-separated line - this checks the
  whole line for a match against ANY single real certification, so a
  fabricated one bundled alongside real ones on the same line wouldn't
  individually be caught. Biases toward under-detection, the safer
  direction.

**Employer context within the PROFESSIONAL EXPERIENCE section**
(2026-08-09, RM's real finding, the dominant false-positive cause - hit
almost every one of Zahir's 28 real resumes): a real resume commonly
states the employer on its OWN line ("SK LIFE SCIENCE, INC. (SKLSI) -
Paramus, NJ") followed by one or more separate sub-role lines with a
title and date but NO employer text repeated ("Head of IT (CIO-
equivalent), January 2024 - January 2026"). Every non-date line inside
the experience section is checked against work_history; the first real
employer name found becomes the "current employer name" until a
DIFFERENT real employer name is found (never reset by an unrelated
line - a candidate's most recently confirmed real employer is a safe,
low-risk default for an unattributed sub-role line, since guessing WHICH
of several possible employers a bare title+date belongs to is far
riskier than assuming continuity with the one just named). A sub-role
date range is checked against the UNION of every real work_history entry
sharing that employer name, not one specific entry - Zahir's actual
profile records multiple internal titles at the same employer as
separate entries with different sub-ranges, and checking against only
one of them would just move the false-positive problem rather than fix
it.

Deliberately NOT covered here (flagged as a real, separate gap, not
silently skipped): freeform numeric claims in prose bullets ("$1B in
annual commercial operations", "reduced OpEx by 30%") and company/
technology mentions inside cover_letter/exec_bio/leadership_summary
prose. Neither has a comparably safe, structurally-anchored way to
distinguish a real (possibly reworded/derived/rounded) fact from a
fabricated one without a real risk of the false positives General
explicitly warned about - treat as a candidate for a future, more
careful Phase 2 rather than an oversight.

A flagged line is folded into the EXACT SAME "?" hedge mechanism the AI's
own uncertain guesses already use - not a parallel detection surface, not
a second review UI. A trailing "?" appended to the flagged line means
tailoring.unconfirmed_claims.find_unconfirmed_markers() (which scans
every exported field's literal text fresh on every call, already wired
into every gate: the "applied" status block, the Apply Assist packet
render, and dossier.sync_workspace_documents' real-filename diversion)
picks it up automatically, with zero changes needed to that gate or its
resolve_unconfirmed_claim() review flow. The only new thing here is HOW a
line ends up with a "?" - a second, deterministic detection path feeding
the one existing downstream pipeline.

Verification discipline (2026-08-09): this module shipped once already
after passing a live check against exactly ONE of Zahir's real resumes -
RM caught, before Zahir ever saw it, that the same check flagged 23/28 of
his real stored resumes for the two structural reasons documented above.
Both this module's test suite and any future change to it should be
re-verified against ALL of his real stored resumes, not a single sample,
before being considered safe to ship again.
"""

import re

from skill_label_match import skills_match
from tailoring.ats_score import STANDARD_HEADERS
from tailoring.docx_export import DATE_RANGE_RE

_EXPERIENCE_HEADERS = {"professional experience", "experience", "work experience"}
_CERTIFICATIONS_HEADER = "certifications"
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_ACRONYM_TOKEN_RE = re.compile(r"\b[A-Z]{2,6}\b")


def _section_for_header(stripped_lower: str) -> str | None:
    """None if this line isn't a real section-boundary line at all (not
    just "is this line all-caps," which would misfire on an all-caps
    acronym-style certification like "PMP" - reuses the exact header set
    ats_score.py's own structure check already recognizes, so the two
    stay in sync by construction)."""
    if stripped_lower not in STANDARD_HEADERS:
        return None
    if stripped_lower in _EXPERIENCE_HEADERS:
        return "experience"
    if stripped_lower == _CERTIFICATIONS_HEADER:
        return "certifications"
    return "other"


def _extract_year(token: str) -> int | None:
    match = _YEAR_RE.search(token)
    return int(match.group(0)) if match else None


def _find_matching_employer_name(text: str, work_history: list[dict]) -> str | None:
    """Checked via skills_match, not a bare substring, same non-fragile
    matcher as every other keyword/skill dedup in this codebase. Returns
    the real profile's own employer string (not the whole entry - a
    named employer can span multiple work_history records, see module
    docstring), or None if `text` doesn't trace back to any of them."""
    for entry in work_history:
        employer = entry.get("employer")
        if employer and skills_match(employer, text):
            return employer
    return None


def _date_range_years(date_match: re.Match) -> tuple[int | None, int | None]:
    start_year = _extract_year(date_match.group("start"))
    end_token = date_match.group("end")
    end_year = None if end_token.lower() == "present" else _extract_year(end_token)
    return start_year, end_year


def _date_range_plausible_for_employer(date_match: re.Match, employer_name: str, work_history: list[dict]) -> bool:
    """Year-granularity containment against the UNION of every real
    work_history entry sharing employer_name (skills_match) - not just
    one entry, since the same real employer can legitimately appear as
    several separate records for different internal titles/sub-ranges
    (confirmed against Zahir's real profile, 2026-08-09). "present"/
    ongoing real end dates don't cap the plausible end year at all."""
    from tailoring.drafting import _parse_work_history_date

    matching_entries = [e for e in work_history if e.get("employer") and skills_match(e["employer"], employer_name)]
    real_starts = [d for d in (_parse_work_history_date(e.get("start")) for e in matching_entries) if d]
    if not real_starts:
        return True  # nothing real to check against - don't flag on missing/unparseable data
    real_start_year = min(d.year for d in real_starts)
    real_ends = [_parse_work_history_date(e.get("end")) for e in matching_entries]
    real_end_year = max((d.year for d in real_ends if d), default=None) if all(real_ends) else None

    drafted_start_year, drafted_end_year = _date_range_years(date_match)
    if drafted_start_year is None:
        return True  # regex matched but no parseable year - don't guess, don't flag
    if drafted_start_year < real_start_year:
        return False
    if real_end_year is not None and drafted_end_year is not None and drafted_end_year > real_end_year:
        return False
    return True


def _certification_matches(drafted_text: str, real_certifications: list[str]) -> bool:
    for real_cert in real_certifications:
        if skills_match(drafted_text, real_cert):
            return True
    # Real gap, 2026-08-09 (RM): a legitimate acronym EXPANSION ("Reltio
    # Master Data Management (MDM) Certification" for profile's "Reltio
    # MDM Certification") fails literal phrase containment even though
    # it's the same real certification, just spelled out. Narrow, safe
    # fallback: if the drafted text and a real certification share an
    # all-caps acronym token (2-6 letters), that's a strong, low-risk
    # signal they're the same credential - deliberately narrower than a
    # general fuzzy/word-overlap match, which would risk matching on an
    # unrelated shared common word instead.
    drafted_acronyms = set(_ACRONYM_TOKEN_RE.findall(drafted_text))
    if not drafted_acronyms:
        return False
    return any(drafted_acronyms & set(_ACRONYM_TOKEN_RE.findall(real_cert)) for real_cert in real_certifications)


def flag_unverified_resume_claims(resume_text: str, profile: dict) -> tuple[str, list[dict]]:
    """Returns (marked_text, flagged) - marked_text is resume_text with a
    trailing "?" appended to any role-header or certification line making
    an unhedged claim that doesn't trace back to the master profile (see
    module docstring for exactly what's checked, where, and what's
    deliberately not). A line already ending in "?" (the AI's own hedge,
    or a prior pass of this same function) is left untouched - never
    double-marked. flagged is [{"skill": None, "text": str}, ...], the
    same shape as the AI's own self-reported unconfirmed_claims list,
    meant to be appended onto resume_unconfirmed_claims_ai_reported by
    the caller (never skill-attributed - these aren't answerable "do you
    have X" gap questions, they're a "this needs a human look" flag)."""
    work_history = profile.get("work_history") or []
    certifications = [c.get("name") for c in (profile.get("certifications") or []) if c.get("name")]

    out_lines = []
    flagged = []
    current_section = None
    current_employer_name = None

    for line in resume_text.splitlines():
        stripped = line.strip()
        stripped_lower = stripped.lower()

        section = _section_for_header(stripped_lower)
        if section is not None:
            current_section = section
            if current_section != "experience":
                current_employer_name = None
            out_lines.append(line)
            continue

        if not stripped or "?" in line:
            out_lines.append(line)
            continue

        if current_section == "experience":
            date_match = DATE_RANGE_RE.search(stripped) if len(stripped) < 120 else None
            if date_match:
                prefix = stripped[: date_match.start()].rstrip(" \t-,")
                employer_name = _find_matching_employer_name(prefix, work_history) if len(prefix) >= 3 else None
                # A sub-role/bare-date line with no employer text of its
                # own falls back to whichever real employer was most
                # recently confirmed on an earlier line in this section -
                # see module docstring's "employer context" section for
                # why this is the safe default rather than flagging.
                effective_employer = employer_name or current_employer_name
                if effective_employer is None or not _date_range_plausible_for_employer(date_match, effective_employer, work_history):
                    out_lines.append(line + "?")
                    flagged.append({"skill": None, "text": stripped})
                    continue
                if employer_name:
                    current_employer_name = employer_name
                out_lines.append(line)
                continue

            # Not a date line - if it's a STRUCTURAL line (not a bullet)
            # naming a real employer, that becomes the context for
            # subsequent sub-role lines with no employer text of their
            # own (see module docstring). Real gap found live 2026-08-09
            # (RM checking all 28 real resumes): a BULLET can legitimately
            # mention a past employer's name as a product/tool, not as
            # this role's employer - "Reltio MDM" (a data-management
            # product Zahir uses at his CURRENT job) coincides with
            # "Reltio," a real PAST employer name, and a bullet mentioning
            # it wrongly overwrote the employer context to "Reltio,"
            # causing the next two real, honest sub-role dates to be
            # checked against the wrong employer's span and flagged.
            # Bullets are free-form accomplishment prose that can mention
            # anything (products, vendors, clients, former employers) -
            # only a company/location header line should ever set this.
            if not stripped.startswith("- "):
                matched = _find_matching_employer_name(stripped, work_history)
                if matched:
                    current_employer_name = matched
            out_lines.append(line)
            continue

        if current_section == "certifications":
            cert_text = stripped[2:].strip() if stripped.startswith("- ") else stripped
            if cert_text and not _certification_matches(cert_text, certifications):
                out_lines.append(line + "?")
                flagged.append({"skill": None, "text": stripped})
                continue

        out_lines.append(line)

    return "\n".join(out_lines), flagged
