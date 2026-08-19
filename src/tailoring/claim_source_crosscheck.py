"""tailoring.claim_source_crosscheck (2026-08-19) - real gap Zahir hit live:
he was asked to re-confirm 4 employment date ranges on a Procept BioRobotics
draft that were already stated verbatim, unhedged, in his own ingested
resume under "SK Life Science, Inc." His point: asking him proves the
question-generation mechanism isn't working - the answer was already on
file.

Root cause (tailoring.baseline_resume.select_baseline_resume_text()): a new
job's resume is very often drafted starting from a PREVIOUSLY DRAFTED resume
for a different application (whichever past draft has the closest keyword
overlap), not fresh from profile.ingest.resume_text()/all_documents_text()
every time. The "?" hedge marker (tailoring.unconfirmed_claims) fires based
on what the reasoner could verify from THAT baseline draft - several steps
removed from the real source documents - so it can both hedge things that
are actually stated correctly in the real ingested resume, and silently
carry forward a drift error introduced in an earlier draft (found live: a
draft said "March 2020", the real resume says "February 2020").

This module is the missing deterministic backstop: before ANY flagged "?"
claim (tailoring.unconfirmed_claims.find_unconfirmed_markers()) is ever
shown to Zahir, cross-check it against profile.ingest.all_documents_text()
- his full real ingested documents, not a derivative draft - and
auto-resolve silently, using the source's own exact wording, whenever the
fact is genuinely verifiable there. Only what's truly absent from every
ingested document still becomes a real question. One batched $0
subscription-CLI call (tailoring.reasoner_cli.run_claude_cli(), never the
paid API) per job covers every flagged claim on that job at once - same
cost shape as gap_question_phrasing.py's rephrase pass, which this module
otherwise mirrors closely (batched call, fail-soft, per-item skill-string
correlation)."""

import json

from tailoring.reasoner_cli import ReasonerUnavailable, parse_json_reply, run_claude_cli

_CROSSCHECK_SYSTEM_PROMPT = (
    "You are fact-checking a candidate's draft resume/cover-letter text against their own real, complete "
    "source documents (full resume plus any other uploaded documents) below. Each flagged line below contains "
    "a hedged guess (marked with a trailing '?') that this app could not verify when it was drafted - it may "
    "have been drafted from an earlier derivative draft rather than these real source documents, so the fact "
    "may already be stated correctly here. For EACH flagged line, check whether the specific hedged detail "
    "(a date, a dollar figure, a duration, a percentage, a tool/product name, anything else uncertain) is "
    "genuinely and unambiguously stated in the source documents below. If it is, return the corrected line "
    "with the hedge resolved using the SOURCE DOCUMENTS' OWN exact wording/figures (fixing the hedged line to "
    "match the source even if that means changing a number the draft guessed differently, as your fix must be "
    "the source's number, not the draft's guess) - never remove the surrounding sentence's other content, only "
    "resolve the specific hedged detail. If the detail is NOT clearly stated in the source documents - not "
    "implied, not approximately inferable - you MUST leave it out of your reply entirely rather than guess; a "
    "real, unresolved question will be asked to the candidate directly for anything you omit. Never fabricate "
    "or approximate a number/date/fact that is not genuinely present in the source text."
)


def _crosscheck_prompt(source_documents_text: str, markers: list[dict]) -> str:
    flagged = [{"index": i, "line": m["line"]} for i, m in enumerate(markers)]
    return "\n\n".join([
        _CROSSCHECK_SYSTEM_PROMPT,
        "CANDIDATE'S REAL SOURCE DOCUMENTS (resume plus any other uploaded documents):\n" + source_documents_text,
        "FLAGGED LINES TO CHECK:\n" + json.dumps(flagged, indent=2),
        "Reply with ONLY a single JSON object (no markdown code fence, no commentary before or after it) with "
        'exactly this key:\n- "resolved": array of objects {"index": integer (exactly matching one of the '
        'flagged lines\' "index" value above), "resolved_line": string (the corrected line, hedge removed, '
        "using the source documents' own exact wording/figures)}. Omit any flagged line entirely from this "
        "array if its hedged detail is not genuinely verifiable in the source documents - do not include a "
        "guess for it.",
    ])


def crosscheck_claims_against_source(markers: list[dict], source_documents_text: str) -> dict[int, str]:
    """Returns {index_into_markers: resolved_line_text} for every flagged
    claim (see tailoring.unconfirmed_claims.find_unconfirmed_markers()'s
    return shape) the reasoner could verify against source_documents_text -
    normally profile.ingest.all_documents_text(). An index missing from the
    result means genuinely not found in source; the caller's existing
    real-question-to-Zahir path handles those unchanged.

    Never raises: mirrors gap_question_phrasing.py's fail-soft precedent -
    this is a pre-check layered in front of an already-working manual
    resolve flow, not a replacement for it. A reasoner failure here
    (ReasonerUnavailable, or RuntimeError from parse_json_reply on a
    malformed/timeout reply) just means zero claims get auto-resolved this
    round; every flagged claim falls through to the existing manual
    confirm/edit panel exactly as it did before this module existed -
    strictly no worse than today, never a missing feature blocking
    someone from clearing a claim by hand."""
    if not markers:
        return {}
    try:
        reply = run_claude_cli(_crosscheck_prompt(source_documents_text, markers))
        data = parse_json_reply(reply)
    except (ReasonerUnavailable, RuntimeError):
        return {}

    resolved: dict[int, str] = {}
    for item in data.get("resolved", []) or []:
        index, resolved_line = item.get("index"), item.get("resolved_line")
        if not isinstance(index, int) or not (0 <= index < len(markers)):
            continue
        if not resolved_line or not resolved_line.strip() or "?" in resolved_line:
            # A "resolved" line that still contains "?" isn't actually
            # resolved - same non-negotiable bar resolve_unconfirmed_claim()
            # itself enforces for a manual edit (raises ValueError on this
            # exact condition). Silently skip rather than pass through a
            # claim that would fail unconfirmed_claims.resolve_unconfirmed_
            # claim()'s own validation a moment later.
            continue
        resolved[index] = resolved_line.strip()
    return resolved
