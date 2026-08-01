"""Normalizes ClinicalTrials.gov MCP connector results into target_account
signals (PRD §16a, signal type 1: late-stage trial progress). Searching
only happens when Claude calls the connector's search_trials/
search_by_sponsor tools live in-session - this module never calls the API
itself, same architectural split as src/search/boards.py for
ZipRecruiter/Dice/Indeed.

Real finding (2026-07-30): a broad phase=PHASE3 + status=[ACTIVE_NOT_
RECRUITING, COMPLETED] + location="United States" query matched 13,659
trials, dominated by mega-pharma (Sanofi, Novartis, GSK, AstraZeneca,
Amgen, Biogen...) and non-company sponsors (universities, hospitals,
government, individual physicians) - ClinicalTrials.gov has no field for
company size or commercial maturity, so phase/status alone can't tell a
company approaching its first launch from an established multinational's
routine pipeline. company_filters.looks_like_target_company() handles the
obvious cases; there's no attempt yet to distinguish a genuinely
small/emerging company from a mid-size one, or to catch research
consortiums/foreign cancer centers that don't match the keyword list -
expected to get refined against Zahir's real reactions to what shows up,
same "start crude, let real usage refine it" pattern as the
compatibility-scoring category-filter lesson (PRD §9).
"""
from datetime import date

from prospector.company_filters import looks_like_target_company

# Real bug found live 2026-07-31: this originally had NO recency filter at
# all - a Phase 3 trial that completed in 2003 or 2005 passed through
# exactly like one from last month, as long as its status was COMPLETED or
# ACTIVE_NOT_RECRUITING. Zahir spotted four bad entries in one glance
# (Pacira Pharmaceuticals off a trial completed 2016-01; two academic/
# cooperative-group sponsors - Gustave Roussy, Radiation Therapy Oncology
# Group - off trials completed 2005 and 2003; VectivBio AG, acquired by
# Ironwood Pharmaceuticals in 2023, off a 2024 completion). Checking the
# real stored data found 8 total watching accounts whose only signal was a
# trial that completed before 2024. A COMPLETED trial years old tells you
# nothing about whether the company is STILL approaching commercialization
# - it may have already been approved, already failed, or the company may
# no longer exist independently. An ACTIVE_NOT_RECRUITING trial with no
# completion date yet is inherently current and kept regardless.
STALE_YEARS = 2


def _is_recent_enough(trial: dict) -> bool:
    if trial.get("status") == "ACTIVE_NOT_RECRUITING":
        return True
    completion = trial.get("primary_completion_date")
    if not completion:
        return False
    try:
        year, month = (int(p) for p in completion.split("-")[:2])
    except (ValueError, AttributeError):
        return False
    cutoff = date.today().replace(day=1)
    cutoff_year = cutoff.year - STALE_YEARS
    return (year, month) >= (cutoff_year, cutoff.month)


def normalize_trial_signals(trials: list[dict]) -> list[dict]:
    """Takes raw items from the search_trials/search_by_sponsor MCP tool
    response and returns signal-ready dicts (company_name + the kwargs
    target_accounts.add_signal expects), skipping non-company sponsors,
    mega-pharma, and trials whose primary completion is more than
    STALE_YEARS old (still-recruiting/active trials always pass - see
    _is_recent_enough). One signal per trial (ref=nct_id dedupes repeat
    runs)."""
    out = []
    for trial in trials:
        sponsor = trial.get("sponsor")
        if not looks_like_target_company(sponsor):
            continue
        if not _is_recent_enough(trial):
            continue
        conditions = ", ".join(trial.get("conditions") or []) or "condition not listed"
        detail = (
            f"{trial.get('nct_id')}: Phase 3 trial ({conditions}) - "
            f"{trial.get('status')}, primary completion {trial.get('primary_completion_date') or 'not listed'}"
        )
        out.append({
            "company_name": sponsor,
            "signal_type": "late_stage_trial",
            "source": "clinicaltrials.gov",
            "detail": detail,
            "ref": trial.get("nct_id"),
        })
    return out
