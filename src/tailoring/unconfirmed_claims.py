"""Zahir's explicit, deliberate exception to "never assert an unconfirmed
guess" (2026-08-09): the resume-writing prompt (drafting.py's
_RESUME_SPEC_COMMON) may now write a plausible-but-unconfirmed fact
directly into resume_text with a trailing "?" - the same hedge convention
already used for clarifying_questions' suggested_answer field - so that
fact's keyword immediately counts toward the real deterministic ATS
score, instead of requiring a full confirm-then-regenerate round trip
first.

What makes this safe, per Zahir's non-negotiable requirement: a hard,
deterministic, code-level gate (find_unconfirmed_markers below) that
scans every exported field's LITERAL text for a "?" character - never
trusting the AI's own self-reported unconfirmed_claims list alone (same
"don't trust the AI's own accounting" principle as every other
deterministic backstop in this codebase, e.g. skill_label_match,
_drop_generic_soft_skill_keywords). Callers (app.py's "applied" status
gate, the Apply Assist packet render, dossier.sync_workspace_documents'
real-filename write) must all check this before treating a document as
final - see each call site's own docstring for exactly what it blocks.
"""

_UNCONFIRMED_MARKER = "?"

# Every stored field the deterministic scanner treats as an exported
# surface a candidate could actually submit or paste somewhere -
# resume_text is where the new hedge-writing behavior above is scoped to,
# but the other three are scanned too, defensively, in case a "?" ever
# ends up in one of them (this module never assumes only one field could
# possibly need it).
_SCANNED_TEXT_FIELDS = ["resume_text", "cover_letter_text", "exec_bio_text", "leadership_summary_text"]


def _lines_with_marker(text: str) -> list[str]:
    return [line for line in text.splitlines() if _UNCONFIRMED_MARKER in line]


def has_unconfirmed_marker(text: str | None) -> bool:
    """Cheap single-field check for dossier.sync_workspace_documents() -
    it needs to know, per doc field, whether THIS text is safe to write
    under its real expected filename, before an app_record with the full
    resume_unconfirmed_claims_ai_reported context necessarily exists yet
    (a fresh draft's app_record isn't upserted until after this runs) -
    see that function's own docstring for the real guarantee this backs."""
    return bool(_lines_with_marker(text or ""))


def find_unconfirmed_markers(app_record: dict) -> list[dict]:
    """The real source of truth for the safety gate - a literal scan of
    every exported field's CURRENT text for a "?" character, recomputed
    fresh every call (never a stored/stale snapshot - same philosophy as
    tailoring.drafting.analyze_fit_before_drafting, so an edit made
    through any box, now or in the future, is reflected on the very next
    call with no separate invalidation step needed).

    Returns [{"field": str, "skill": str | None, "line": str}, ...] - one
    entry per line containing a "?", not per "?" character (a resume line
    is the natural, unambiguous edit/confirm unit here, matching how
    docx_export.py already treats lines as bullet units). "field" is
    "resume_text"/"cover_letter_text"/"exec_bio_text"/
    "leadership_summary_text", or "apply_answers:{label}" for a flagged
    apply_answers entry. "skill" is looked up from
    resume_unconfirmed_claims_ai_reported (the AI's own self-report, saved
    once at generate time) by matching that reported claim's exact text
    against the line - falls back to None when nothing matches, which
    happens either because the AI under-reported a "?" it wrote, or
    because a human hand-edited the text and introduced/left one the AI
    never knew about. Either way the line still shows up here, flagged -
    a missing skill label never means a missing entry."""
    ai_reported = app_record.get("resume_unconfirmed_claims_ai_reported") or []
    flagged = []
    for field in _SCANNED_TEXT_FIELDS:
        text = app_record.get(field) or ""
        for line in _lines_with_marker(text):
            stripped_line = line.strip()
            skill = next(
                (c.get("skill") for c in ai_reported if c.get("text") and c["text"].strip() in stripped_line),
                None,
            )
            flagged.append({"field": field, "skill": skill, "line": stripped_line})

    for item in app_record.get("apply_answers") or []:
        value = item.get("value") or ""
        label = item.get("label") or "unlabeled field"
        for line in _lines_with_marker(value):
            flagged.append({"field": f"apply_answers:{label}", "skill": None, "line": line.strip()})

    return flagged


def resolve_unconfirmed_claim(job: dict, app_record: dict, claim: dict, action: str, new_text: str | None = None) -> dict:
    """Resolves ONE flagged claim (a {"field", "skill", "line"} dict from
    find_unconfirmed_markers) by either restating it as a confirmed fact
    (action="edit", new_text replaces claim["line"] verbatim - must be
    real text with no remaining "?") or accepting the hedged guess as
    literally true (action="confirm" - strips just that line's trailing
    "?", nothing else about the wording changes). Either way, this is a
    genuine confirmed fact now, so it's persisted into gap_interview_
    answers via profile/interview.py's save_answer() - the same mechanism
    every other confirmed answer in this app already uses, not a parallel
    one-off store just for this feature.

    Returns {field: new_field_text} for the caller to persist via
    upsert_application() and use to decide whether to re-sync the
    workspace .docx (once a job's full find_unconfirmed_markers() list is
    empty) - for an apply_answers claim (field starts with
    "apply_answers:"), the key is always the real field name
    "apply_answers" with its full updated list, not the compound
    "apply_answers:{label}" claim identifier. This function itself does
    not touch applications.json,
    matching this codebase's existing boundary (drafting.py's
    save_gap_answers only ever writes to the profile store; app.py owns
    every applications.json write - see save_gap_answers' own docstring).
    Raises ValueError on an unknown action or an edit that still contains
    a "?" (an "edit" that doesn't actually resolve anything is a bug at
    the call site, not something to silently accept)."""
    from datetime import datetime, timezone

    from profile.interview import save_answer

    if action == "confirm":
        resolved_line = claim["line"].rstrip("?").rstrip()
    elif action == "edit":
        if not new_text or not new_text.strip():
            raise ValueError("new_text must be real, non-empty replacement text.")
        if "?" in new_text:
            raise ValueError("new_text must not contain '?' - it's meant to state the confirmed fact, not another guess.")
        resolved_line = new_text.strip()
    else:
        raise ValueError(f"Unknown action: {action!r} (expected 'edit' or 'confirm').")

    field = claim["field"]
    if field.startswith("apply_answers:"):
        label = field.split(":", 1)[1]
        updated_items = [
            {**item, "value": resolved_line} if (item.get("label") or "unlabeled field") == label and claim["line"] in (item.get("value") or "")
            else item
            for item in (app_record.get("apply_answers") or [])
        ]
        result = {"apply_answers": updated_items}
    else:
        text = app_record.get(field) or ""
        updated_lines = [
            resolved_line if line.strip() == claim["line"] else line
            for line in text.splitlines()
        ]
        result = {field: "\n".join(updated_lines)}

    skill = claim.get("skill") or resolved_line
    role_context = f"{job.get('title', 'Unknown role')} at {job.get('organization', 'Unknown organization')}"
    save_answer(
        skill=skill, role_context=role_context, answer=resolved_line,
        date_captured=datetime.now(timezone.utc).date().isoformat(),
        question=f"Confirm: {claim['line']}",
    )

    return result
