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

- Resume role-header lines (the ones docx_export.py's DATE_RANGE_RE
  already recognizes, e.g. "SK Life Science, Inc. (SKLSI)   01/2024 -
  01/2026") are checked two ways: the employer text must trace back
  (skills_match) to a real profile.work_history entry, and if it does,
  the drafted date range's YEARS must fall within that same entry's real
  [start, end] span. Year-granularity only, not month-precise - the
  drafted text renders "Month YYYY" while the profile stores "MM/YYYY"/
  "YYYY", and month-level reconciliation between two different formats
  is exactly the kind of precision this module has no business claiming;
  a year-level check still catches a genuinely wrong/invented range
  without risking a false positive over an off-by-one-month rounding.
- Certification lines (inside a CERTIFICATIONS section) must trace back
  (skills_match) to a real profile.certifications entry. Known gap, found
  live against Zahir's real resume (2026-08-09): his drafted
  CERTIFICATIONS section bundles all of them onto ONE " | "-separated
  line - this checks the whole line for a match against ANY single real
  certification, so a fabricated one bundled alongside real ones on the
  same line wouldn't currently be individually caught. Biases toward
  under-detection there, the safer direction, rather than splitting the
  line and risking a false positive on a real cert whose exact wording
  doesn't split cleanly.
- A role-header line's date range is only checked when there's real
  employer/title TEXT before the date on the SAME line. A bare date
  range sitting alone on its own line (a genuine, common layout - see
  the len(prefix) < 3 guard below) is never checked at all, since there's
  nothing on that line to trace an employer from and guessing which
  earlier line it belongs to is exactly the kind of confident-but-wrong
  proximity heuristic this module exists to avoid. Found live against
  Zahir's real resume before shipping (2026-08-09): 4 false positives on
  one completely honest resume before this guard existed.

Deliberately NOT covered here (flagged as a real, separate gap, not
silently skipped): freeform numeric claims in prose bullets ("$1B in
annual commercial operations", "reduced OpEx by 30%") and company/
technology mentions inside cover_letter/exec_bio/leadership_summary
prose. Both lack a comparably safe, structurally-anchored way to
distinguish a real (possibly reworded/derived/rounded) fact from a
fabricated one without a real risk of the false positives General
explicitly warned about - see this module's own docstring in the commit
this shipped with for the full reasoning, and treat as a candidate for a
future, more careful Phase 2 rather than an oversight.

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
"""

import re

from skill_label_match import skills_match
from tailoring.ats_score import STANDARD_HEADERS
from tailoring.docx_export import DATE_RANGE_RE

_CERTIFICATIONS_HEADER_RE = re.compile(r"^certifications?$", re.IGNORECASE)
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _is_section_header_line(stripped_line: str) -> bool:
    """A real section-boundary line (PROFESSIONAL EXPERIENCE, EDUCATION,
    etc.) - NOT a bare "is this line all-caps" check, which would
    misfire on an all-caps acronym-style certification like "PMP" and
    incorrectly end the certifications section on its very first real
    entry. Reuses the exact header set ats_score.py's own structure
    check already recognizes, so the two stay in sync by construction."""
    return stripped_line.lower() in STANDARD_HEADERS


def _extract_year(token: str) -> int | None:
    match = _YEAR_RE.search(token)
    return int(match.group(0)) if match else None


def _find_matching_employer(prefix: str, work_history: list[dict]) -> dict | None:
    """The role-header line's text before the date range should mention a
    real employer SOMEWHERE in it (title and company aren't split by any
    fixed separator the AI reliably uses - see docx_export.py's own
    "Company/role lines" comment) - checked via skills_match, not a bare
    substring, same non-fragile matcher as every other keyword/skill dedup
    in this codebase. Returns the first matching real entry, or None if
    this line doesn't trace back to any of them at all."""
    for entry in work_history:
        employer = entry.get("employer")
        if employer and skills_match(employer, prefix):
            return entry
    return None


def _date_range_plausible(date_match: re.Match, real_entry: dict) -> bool:
    """Year-granularity containment check - see module docstring for why
    year-level, not month-level. "present"/ongoing end dates are treated
    as satisfied automatically (no fixed year to compare, and a still-
    ongoing real role can't be "too early" - only a bounded, wrong-looking
    range is checkable here)."""
    from tailoring.drafting import _parse_work_history_date

    real_start = _parse_work_history_date(real_entry.get("start"))
    real_end = _parse_work_history_date(real_entry.get("end"))
    if not real_start:
        return True  # nothing real to check against - don't flag on missing data

    drafted_start_year = _extract_year(date_match.group("start"))
    drafted_end_token = date_match.group("end")
    drafted_end_year = None if drafted_end_token.lower() == "present" else _extract_year(drafted_end_token)

    if drafted_start_year is None:
        return True  # regex matched but no parseable year - don't guess, don't flag
    if drafted_start_year < real_start.year:
        return False
    if real_end and drafted_end_year is not None and drafted_end_year > real_end.year:
        return False
    return True


def flag_unverified_resume_claims(resume_text: str, profile: dict) -> tuple[str, list[dict]]:
    """Returns (marked_text, flagged) - marked_text is resume_text with a
    trailing "?" appended to any role-header or certification line making
    an unhedged claim that doesn't trace back to the master profile (see
    module docstring for exactly what's checked and what's deliberately
    not). A line already ending in "?" (the AI's own hedge, or a prior
    pass of this same function) is left untouched - never double-marked.
    flagged is [{"skill": None, "text": str}, ...], the same shape as the
    AI's own self-reported unconfirmed_claims list, meant to be appended
    onto resume_unconfirmed_claims_ai_reported by the caller (never skill-
    attributed - these aren't answerable "do you have X" gap questions,
    they're a "this needs a human look" flag) so find_unconfirmed_markers'
    downstream field lookup has something to find, though it degrades
    gracefully to skill=None either way."""
    work_history = profile.get("work_history") or []
    certifications = [c.get("name") for c in (profile.get("certifications") or []) if c.get("name")]

    out_lines = []
    flagged = []
    in_certifications_section = False

    for line in resume_text.splitlines():
        stripped = line.strip()

        if _CERTIFICATIONS_HEADER_RE.match(stripped):
            in_certifications_section = True
            out_lines.append(line)
            continue
        if in_certifications_section and _is_section_header_line(stripped):
            in_certifications_section = False  # a new real section header ends this block

        if not stripped or "?" in line:
            out_lines.append(line)
            continue

        date_match = DATE_RANGE_RE.search(stripped) if len(stripped) < 120 else None
        if date_match:
            prefix = stripped[: date_match.start()].rstrip(" \t-")
            if len(prefix) < 3:
                # Real gap caught by live-checking Zahir's actual resume
                # before shipping this (2026-08-09): a bare date range
                # sitting alone on its own line - "January 2024 - January
                # 2026" with nothing before it - is a genuine, common
                # layout (a sub-role's dates under a company/title stated
                # on an EARLIER line, not repeated here). There's no
                # employer text on THIS line to check at all, so this
                # isn't "doesn't trace back" - it's "nothing to check
                # against," and treating those the same produced 4 false
                # positives on one real, completely honest resume.
                # Deliberately fails open (never flags) rather than
                # guessing which earlier line's employer this belongs to
                # - a proximity heuristic risks exactly the kind of
                # confident-but-wrong guess this module exists to avoid.
                out_lines.append(line)
                continue
            real_entry = _find_matching_employer(prefix, work_history)
            if real_entry is None or not _date_range_plausible(date_match, real_entry):
                out_lines.append(line + "?")
                flagged.append({"skill": None, "text": stripped})
                continue
            out_lines.append(line)
            continue

        if in_certifications_section:
            cert_text = stripped[2:].strip() if stripped.startswith("- ") else stripped
            if cert_text and not any(skills_match(cert_text, real_cert) for real_cert in certifications):
                out_lines.append(line + "?")
                flagged.append({"skill": None, "text": stripped})
                continue

        out_lines.append(line)

    return "\n".join(out_lines), flagged
