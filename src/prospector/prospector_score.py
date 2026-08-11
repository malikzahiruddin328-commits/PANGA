"""Prospector Score (Zahir's request 2026-07-30): one headline number
summarizing how well the whole proactive job-search system - target
accounts, outreach, applications, real outcomes - is working right now.

Same "Python gathers, Claude reasons" split as fit_score/profile_strength_
score/every other judgment call in Panga: this module never computes the
score itself, only assembles the underlying data (reusing the existing
Learn Engine bundle and KPI summaries rather than re-deriving anything).

This is deliberately framed as self-learning, not a static formula - it's
meant to be a real product differentiator, so whenever this score is
(re)computed, the rationale must say what real data it's grounded in and
that it sharpens as more outcomes accumulate, not just report a number.
Early on, with few real outcomes tracked, an honest low-confidence score
with that caveat stated plainly is correct behavior, not a bug - same
principle as the Learn Engine's own "thin at first" caveat.

Every score also needs `next_actions` (Zahir's request 2026-07-30): a short
list of concrete, specific things to actually do to raise it - not vague
encouragement. Unlike the LinkedIn suggestions (which rewrite text that
lives outside Panga on LinkedIn's own site, so completion has to be
manually marked), the actions here are about Panga's OWN tracked data
(target account status, outreach, applications) - so there's no separate
completion-tracking needed. Just re-running gather_prospector_score_input()
and asking for a fresh score naturally "pulls" whatever's changed, since it
re-reads these stores live every time, not a cached snapshot.
"""

import json
from pathlib import Path

from llm_client import DEFAULT_MODEL, LLMCallFailed as DraftingFailed, call_structured, get_client as _client
from prospector.learn_engine import gather_learn_engine_input
from prospector.kpis import activity_summary, coverage_summary, outcome_summary
from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCORE_PATH = PROJECT_ROOT / "data" / "prospector" / "prospector_score.json"

_DEFAULT = {"score": None, "rationale": None, "next_actions": None, "data_points": None, "computed_at": None}


def load_prospector_score() -> dict:
    data = read_json(SCORE_PATH, default=None)
    if data is None:
        return dict(_DEFAULT)
    for key, default_value in _DEFAULT.items():
        data.setdefault(key, default_value)
    return data


def save_prospector_score(score: int, rationale: str, next_actions: list[str], data_points: int, computed_at: str) -> None:
    write_json(SCORE_PATH, {
        "score": score,
        "rationale": rationale,
        "next_actions": next_actions,
        "data_points": data_points,
        "computed_at": computed_at,
    })


def gather_prospector_score_input(
    applications: list[dict],
    jobs: list[dict],
    target_accounts: list[dict],
    outreach: list[dict],
    interview_prep_records: list[dict],
    target_roles: list[dict] | None = None,
) -> dict:
    learn_bundle = gather_learn_engine_input(applications, jobs, target_accounts, outreach, interview_prep_records)
    data_points = sum(len(learn_bundle[k]) for k in (
        "scoring_vs_outcome", "target_account_vs_outcome", "outreach_vs_outcome", "interview_outcomes",
    ))

    target_account_status_counts: dict[str, int] = {}
    for acc in target_accounts:
        target_account_status_counts[acc["status"]] = target_account_status_counts.get(acc["status"], 0) + 1

    outreach_status_counts: dict[str, int] = {}
    for o in outreach:
        outreach_status_counts[o["status"]] = outreach_status_counts.get(o["status"], 0) + 1

    return {
        "coverage": coverage_summary(jobs),
        "activity": activity_summary(applications),
        "outcome": outcome_summary(applications, jobs, target_roles),
        "target_account_status_counts": target_account_status_counts,
        "outreach_status_counts": outreach_status_counts,
        "learn_engine_bundle": learn_bundle,
        "data_points": data_points,
    }


_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": (
                "0-100 headline Prospector Score - how well the whole proactive "
                "job-search system (target accounts, outreach, applications, "
                "real outcomes) is working right now. Be honest, not generous - "
                "with few real outcomes tracked, a low, low-confidence score is "
                "correct behavior, not a bug."
            ),
        },
        "rationale": {
            "type": "string",
            "description": "2-4 sentences: what real data this is grounded in, and how confident it is given how much of that data exists so far.",
        },
        "next_actions": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "2-5 concrete, specific things to actually do to raise the "
                "score, using Panga's own tracked data (target account status, "
                "outreach, applications) - not vague encouragement."
            ),
        },
    },
    "required": ["score", "rationale", "next_actions"],
    "additionalProperties": False,
}


def compute_prospector_score(input_data: dict, on_progress=None) -> dict:
    """Computes the Prospector Score directly via the Claude API (Zahir's
    explicit ask 2026-07-31: clicking the button produced "Data's ready. Go
    to Claude Code and ask it to compute it" instead of an actual result -
    the same friction point behind the original "Request documents" button
    fixed in tailoring/drafting.py). Same deliberate, narrow exception to
    "Python orchestrates, Claude reasons live" as that module, for the same
    reason: no live Claude Code session backs the Streamlit process itself,
    so a synchronous in-app result needs a direct API call. Returns
    {"score": int, "rationale": str, "next_actions": [...]}; raises
    DraftingNotConfigured (via _client()) if no API key is set,
    DraftingFailed on refusal/API error.

    If given, on_progress(substatus: str) is called with a live "thinking..."
    / "writing... (N characters so far)" status as the response streams in -
    same real-progress mechanism as tailoring/drafting.py's document
    generation (Zahir's explicit ask 2026-07-31: no spinner anywhere should
    be opaque when the underlying call can report real progress instead)."""
    client = _client()
    data = call_structured(
        client,
        system=(
            "You compute a self-learning Prospector Score for a job search's "
            "proactive-outreach system by reasoning over the real data "
            "provided - never a fixed formula. Score honestly; a thin score "
            "grounded in few real outcomes is correct, not something to "
            "inflate for encouragement."
        ),
        user_content=json.dumps(input_data, indent=2, default=str),
        schema=_SCORE_SCHEMA,
        max_tokens=2000,
        model=DEFAULT_MODEL,
        effort="medium",
        thinking=False,
        on_progress=on_progress,
        refusal_message="Claude declined to compute a score. Try again.",
        purpose="prospector_score",
    )
    return {"score": data["score"], "rationale": data["rationale"], "next_actions": data["next_actions"]}
