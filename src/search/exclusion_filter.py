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
_IC_TIER_PATTERN = re.compile(
    r"\b(engineer|analyst|specialist|consultant|scientist|representative|coordinator|associate)\b",
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
    r"|\blab tech\b",
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


def check_exclusion(job: dict, custom_exclusions: list[str] | None = None) -> dict | None:
    """Returns {"rule": ..., "reason": ...} if this job should never be
    persisted, or None if it should go through job_store.save_jobs()'s
    normal path. All four layers are checked independently (not
    short-circuit on an earlier layer's verdict) - see this module's own
    docstring on why "Medical Director" needs layer 2 to fire regardless
    of layer 1; layers 3 (the user's own custom terms) and 4 (generic
    administrative/support titles) are likewise checked even when earlier
    layers already passed, so either can catch a title the others
    wouldn't.

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
