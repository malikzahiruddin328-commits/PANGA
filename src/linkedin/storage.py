"""Local encrypted store for LinkedIn profile enhancement (PRD §13): Zahir
uploads a PDF export of his current LinkedIn profile (LinkedIn's own "Save to
PDF" and/or a browser print-to-PDF of the profile page - both things he
generates himself from his own logged-in session), Claude compares the
extracted text against the master profile + role_skills and drafts specific
suggested rewrites, Zahir copies those back into LinkedIn's own edit screens
himself. Same manual-export-only constraint as everywhere else - no
scraping, no login, no API calls to linkedin.com in either direction.
Encrypted at rest (PRD §7) via security.crypto_store, same as the other
personal-data stores - this holds a full profile export, at least as
sensitive as the resume text in master_profile.json.

Suggestions are section-scoped (headline/about/experience/skills - multiple
allowed for experience since it can span several roles/bullets) and carry a
status lifecycle like applications.py: "suggested" -> "applied" (Zahir
copied it into LinkedIn) or "dismissed".

Every read-modify-write function below runs inside security.file_lock.
locked("linkedin_profile") (found missing 2026-08-10, PRD S13 #25 - same
audit lens as the outreach.json/target_accounts.json fixes this same
night, just not caught in the first pass since it's a different domain).
Real concurrent-writer exposure: Streamlit serves each open browser tab
of the same running process on its own thread, and all three writers here
(save_snapshot() from Settings' uploader, save_analysis() from the
LinkedIn tab's "Analyze profile" button, mark_suggestion_status() from
its Mark updated/Dismiss buttons) are reachable from a normal Streamlit
session - two tabs open at once, or two rapid clicks landing in
overlapping reruns, can race on this file exactly like any other store in
this app.
"""

from pathlib import Path

from security.crypto_store import read_json, write_json
from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LINKEDIN_PATH = PROJECT_ROOT / "data" / "linkedin" / "linkedin_profile.json"

SECTIONS = ("headline", "about", "experience", "skills", "certifications")

_DEFAULT = {
    "raw_text": "",
    "source_files": [],
    "last_saved": None,
    "last_analyzed": None,
    "profile_strength_score": None,
    "profile_strength_rationale": None,
    "suggestions": [],
}


def load_linkedin_profile() -> dict:
    data = read_json(LINKEDIN_PATH, default=None)
    if data is None:
        return dict(_DEFAULT)
    for key, default_value in _DEFAULT.items():
        data.setdefault(key, default_value)
    return data


def _save(data: dict) -> None:
    write_json(LINKEDIN_PATH, data)


def save_snapshot(raw_text: str, source_files: list[str], saved_at: str) -> None:
    """Called after the Streamlit uploader extracts text from the PDF(s) -
    replaces the raw profile text + which file(s) it came from. Leaves any
    prior suggestions in place until the next live analysis pass replaces
    them via save_analysis()."""
    with locked("linkedin_profile"):
        data = load_linkedin_profile()
        data["raw_text"] = raw_text
        data["source_files"] = source_files
        data["last_saved"] = saved_at
        _save(data)


def _suggestion_identity(s: dict) -> tuple:
    """Stable-ish identity used to carry status across a re-analysis (see
    save_analysis below) - current_text quotes the profile's OWN existing
    wording for that section, so it reproduces identically across
    re-analyses of the same underlying profile export even though an LLM's
    suggested_text/rationale wording won't. A suggestion that proposes
    something new rather than editing existing text (e.g. a skill that
    isn't listed at all) has no current_text to anchor on, so those match
    on suggested_text instead."""
    section = s.get("section")
    current_text = (s.get("current_text") or "").strip()
    if current_text:
        return (section, "current", current_text)
    return (section, "suggested", (s.get("suggested_text") or "").strip())


def save_analysis(
    suggestions: list[dict],
    profile_strength_score: int,
    profile_strength_rationale: str,
    analyzed_at: str,
) -> None:
    """Called by Claude at the end of a live enhancement pass (per PRD §11
    LLM architecture - Python never generates this content itself). Each
    suggestion dict needs: section (one of SECTIONS), current_text,
    suggested_text, rationale. Replaces the whole suggestions list - a fresh
    analysis supersedes the prior one rather than accumulating stale items -
    but carries forward "applied"/"dismissed" status by matching suggestion
    identity (see _suggestion_identity) against the prior list first (real
    bug, Mirror's proactive sweep 2026-08-08: re-running analysis used to
    silently reset every suggestion back to "suggested", wiping out
    anything Zahir had already marked applied or dismissed, no warning -
    same failure shape as the resume-regenerate bug, caught before it
    actually fired for him). Assigns a stable id per suggestion (section +
    index within that section) so mark_suggestion_status() can target one
    precisely - the id itself isn't meant to survive a re-analysis, only
    the applied/dismissed status is."""
    with locked("linkedin_profile"):
        data = load_linkedin_profile()
        prior_status_by_identity = {_suggestion_identity(s): s["status"] for s in data["suggestions"]}
        per_section_counter = {}
        stamped = []
        for s in suggestions:
            section = s["section"]
            idx = per_section_counter.get(section, 0)
            per_section_counter[section] = idx + 1
            new_suggestion = {
                "id": f"{section}_{idx}",
                "section": section,
                "current_text": s.get("current_text", ""),
                "suggested_text": s["suggested_text"],
                "rationale": s.get("rationale", ""),
                "status": "suggested",
            }
            carried_status = prior_status_by_identity.get(_suggestion_identity(new_suggestion))
            if carried_status in ("applied", "dismissed"):
                new_suggestion["status"] = carried_status
            stamped.append(new_suggestion)
        data["suggestions"] = stamped
        data["profile_strength_score"] = profile_strength_score
        data["profile_strength_rationale"] = profile_strength_rationale
        data["last_analyzed"] = analyzed_at
        _save(data)


def mark_suggestion_status(suggestion_id: str, status: str) -> None:
    """status is "applied" (Zahir copied the suggested text into LinkedIn
    himself) or "dismissed" (he's not taking it) - either way it drops out of
    get_active_suggestions() so it isn't shown again."""
    with locked("linkedin_profile"):
        data = load_linkedin_profile()
        for s in data["suggestions"]:
            if s["id"] == suggestion_id:
                s["status"] = status
                _save(data)
                return


def get_active_suggestions() -> list[dict]:
    data = load_linkedin_profile()
    return [s for s in data["suggestions"] if s["status"] == "suggested"]
