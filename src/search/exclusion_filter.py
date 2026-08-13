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
   Scientist/Pharmacology, Medical Science Liaison, Medical Advisor -
   verified: "Senior Medical Director, Hematology Clinical Development" at
   AbbVie scored 0 four separate times with an identical "clinical role,
   no domain overlap" rationale. This layer is deliberately independent of
   layer 1 - "Medical Director" carries an executive-qualifying word
   ("Director") that would otherwise keep it, so the clinical check must
   run regardless of the seniority verdict, not only when seniority
   already excluded it.
3. Project/Program/Product management track exclusion (added 2026-08-13,
   Zahir's explicit request): PM/PgM/ProdM is a DIFFERENT career track
   from Zahir's IT-leadership target (CIO/CISO/Director-VP-Head of IT)
   even at Director/VP level - "Project Director," "Program Director,"
   "VP of Product Management" are all genuinely senior titles that would
   otherwise SURVIVE layer 1 (which only filters IC-level titles, not
   Director+/VP+), so this needs its own independent layer, same as
   clinical. Scoped narrowly to the literal noun phrase (project/program/
   product immediately followed by manager/management/director, or the
   PMO acronym) specifically so it does NOT catch a real validated KEEP
   like "Director, IT Service Continuity" or "IT Director, Vendor
   Management" (neither contains "project/program/product" immediately
   before "manager/management/director") - validated 2026-08-13 against
   the full live job store (140 real occurrences / 113 unique titles
   matched, incl. "Project Director," "DHS PROGRAM DIRECTOR 4 - 79704,"
   "VP of Product Management, Monetization," "Head of Product Management
   - Intelligence Ventures" - the exact real examples Zahir flagged from
   the review queue).
4. Intern/internship exclusion (added 2026-08-13): any title indicating
   the posting itself IS an internship/entry-level intern role. Reuses
   layer 1's _EXEC_QUALIFIER_PATTERN as an exemption, same shape as the
   seniority layer's own IC-noun/exec-qualifier logic - a title
   containing "internship" that ALSO carries an executive-qualifying word
   ("Dietitian (Dietetic Internship Director)," a real title in the live
   store) is a role that DIRECTS an internship program, not an intern
   position, and must not be caught; plain intern postings ("Intern -
   Biotechnologist (Protein)," "Fall 2026 IT Intern (...)") carry no such
   qualifier and are excluded.
5. Information-security-domain exclusion (added 2026-08-13, Zahir's
   explicit request after manually rejecting "VP, Information Security
   and Compliance" (Veritone Corp) and "Director of Information Security
   (Hybrid)" (SAGE Dining Services) from the live review queue and saying
   "add that to the filter as well"). Originally scoped narrowly to the
   literal "information security"/"infosec" phrase; broadened the same
   day (Zahir's explicit option-B choice on a direct two-option question)
   to match ANY title containing the word "security" at all - including
   combined IT+Security leadership titles like "Director, IT & Security"
   and "Head of Infrastructure & Security," not just pure
   security-specialist titles. Verified against the real live job store:
   35 real titles containing "security" but not "information security"
   were being missed under the old narrow pattern (examples: "API
   Security Engineer," "Director of IT Platforms & Security," "Director,
   IT & Security," "Head of Cyber Security," "Head of Infrastructure &
   Security," "IT Security Director," "SVP, Network Security Engineering
   Lead," "Transportation Security Officer," "VP HR, Safety & Security,"
   "Vice President, Software Supply Chain Security") - all now excluded
   under the broadened pattern.

   This layer originally exempted the literal "Chief Information Security
   Officer" phrase and the bare "CISO" abbreviation from exclusion, on the
   reasoning that fit_score (not search-time filtering) should be the
   layer that scores CISO roles low. Zahir explicitly overrode that
   2026-08-13: "ciso and security... must be excluded from the initial
   fetch" - he wants CISO excluded at search/fetch time too, not just
   scored low later. This matches his own real profile data
   (`data/profile/structured/master_profile.json`): "Zahir does NOT
   consider himself qualified for CISO-titled roles specifically...
   Score/recommend CISO-titled roles low regardless of subject-matter
   proximity - this is a real disqualifier, not just a preference." The
   CISO exemption is therefore removed - "ciso" titles are now caught
   directly by the broadened "security" pattern anyway (every real CISO
   title contains the literal word "security" - "Chief Information
   Security Officer"), so there's no longer a separate ciso-specific term
   in the pattern at all.

   CIO must still survive - this is the one boundary that does NOT
   change. "Chief Information Officer" and its variants (Zahir's real
   target role - confirmed via his profile's discussion of "Chief
   Information Officer (CIO) at National Endowment for the Humanities" as
   a genuine role of interest) contain no "security" substring, so a
   CIO-only title is never touched by this layer either way.

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

from security.crypto_store import read_json, write_json
from security.file_lock import locked

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXCLUSION_LOG_PATH = PROJECT_ROOT / "data" / "jobs" / "search_exclusion_log.json"

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
    r"|\bmedical advisor\b",
    re.I,
)

# Layer 3: project/program/product management track exclusion. Deliberately
# order-sensitive (project/program/product must come BEFORE
# manager/management/director) so it does NOT catch a title where "Director"
# precedes an unrelated "Product"/"Program" word (e.g. "Director, Product
# Engineering" never matches - "product" isn't followed by
# manager/management/director there), and does not touch a validated KEEP
# like "Director, IT Service Continuity" or "IT Director, Vendor Management"
# at all (neither contains "project"/"program"/"product" anywhere).
_PM_TRACK_PATTERN = re.compile(
    r"\b(?:project|program|product)\s+(?:manager|management|director)\b"
    r"|\bpmo\b",
    re.I,
)

# Layer 4: intern/internship exclusion. Reuses layer 1's
# _EXEC_QUALIFIER_PATTERN as an exemption so a title that DIRECTS an
# internship program ("Dietitian (Dietetic Internship Director)") is not
# mistaken for an intern position itself.
_INTERN_PATTERN = re.compile(r"\bintern\b|\binternship\b", re.I)

# Layer 5: information-security-domain exclusion. Broadened 2026-08-13
# (Zahir's explicit option-B choice on a direct two-option question) from
# the original narrow "information security"/"infosec" phrase match to
# ANY title containing the standalone word "security" - this deliberately
# now catches combined IT+Security leadership titles ("Director, IT &
# Security," "Head of Infrastructure & Security") as well as pure
# security-specialist titles, not just an "information security" domain
# phrase. Still word-boundary matched so it never fires on "security"
# appearing as part of a different word. Keeps the "infosec" alternative
# alongside the broad "security" match - "infosec" (a real live-store
# abbreviation, e.g. "IT Spec (Infosec), GS-2210-14") doesn't contain the
# literal substring "security", so it would otherwise be silently dropped
# by the broadening. "Chief Information Security Officer"/"CISO" titles
# are caught by the "security" alternative now (every real CISO title
# contains the literal word "security"), so there is no separate
# ciso-specific term needed. CIO must still survive - "Chief Information
# Officer" and its variants contain no "security"/"infosec" substring, so
# this layer never touches a CIO-only title either way.
_INFO_SECURITY_PATTERN = re.compile(
    r"\bsecurity\b|\binfosec\b",
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


def _pm_track_exclude(title: str) -> str | None:
    match = _PM_TRACK_PATTERN.search(title)
    if match:
        return f"project/program/product management track (matched \"{match.group(0)}\")"
    return None


def _intern_exclude(title: str) -> str | None:
    if _INTERN_PATTERN.search(title) and not _EXEC_QUALIFIER_PATTERN.search(title):
        return "intern/internship-tier title with no executive-qualifying word present"
    return None


def _information_security_exclude(title: str) -> str | None:
    match = _INFO_SECURITY_PATTERN.search(title)
    if match:
        return (
            "security-domain role (matched "
            f'"{match.group(0)}" - broadened 2026-08-13 to any title'
            " containing \"security\", including combined IT+Security"
            " leadership titles and CISO titles, a real disqualifier"
            " per Zahir's own profile data, not just a lower-fit-score"
            " preference)"
        )
    return None


def check_exclusion(job: dict) -> dict | None:
    """Returns {"rule": ..., "reason": ...} if this job should never be
    persisted, or None if it should go through job_store.save_jobs()'s
    normal path. All five layers are independent checks (not short-
    circuited on an earlier layer's verdict) - see this module's own
    docstring on why "Medical Director" needs layer 2 to fire regardless
    of layer 1. check_exclusion() returns the first rule that matches, in
    layer order, purely for a single deterministic label per job - a title
    can trip more than one layer (e.g. "IT PMO Consultant..." matches both
    layer 1's IC-tier "Consultant" and layer 3's PM-track pattern) and is
    excluded either way."""
    title = job.get("title") or ""

    reason = _seniority_exclude(title)
    if reason:
        return {"rule": "seniority_mismatch", "reason": reason}

    reason = _clinical_exclude(title)
    if reason:
        return {"rule": "clinical_domain", "reason": reason}

    reason = _pm_track_exclude(title)
    if reason:
        return {"rule": "pm_track_mismatch", "reason": reason}

    reason = _intern_exclude(title)
    if reason:
        return {"rule": "intern_role", "reason": reason}

    reason = _information_security_exclude(title)
    if reason:
        return {"rule": "information_security_domain", "reason": reason}

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
