"""TEMPORARY stand-ins for docs/score-first-resume-flow-spec.md's backend
pieces (items 1/2/4/7 - ATS Engine's work, contract confirmed with them
2026-08-08, not yet built). Kept in their own module, separate from the
real logic in ats_score.py/drafting.py, so the eventual swap is a single
import-line change in ui/app.py rather than a diff against real code -
delete this whole file once tailoring.drafting.analyze_fit_before_drafting()
and its item-6/7 counterpart exist for real, and update the two import
sites in ui/app.py.

Also intentionally NOT defined inside ui/app.py itself: Streamlit's
AppTest re-executes app.py's source fresh on every .run() rather than
reusing the already-imported module object, so monkeypatching a function
defined directly in app.py from a test never actually takes effect (the
freshly re-executed script rebinds its own name to the original,
unpatched code every time) - a real, reproducible AppTest limitation
found in this exact spot 2026-08-08, not a subtle test-writing mistake.
Functions imported by app.py FROM a separate real module (like this one,
same as tailoring.drafting.generate_documents already is) patch correctly,
since app.py's `from ... import ...` line re-reads the module's
CURRENT (possibly monkeypatched) attribute value on every fresh exec."""


def analyze_fit_before_drafting(job: dict, profile: dict) -> dict:
    """See module docstring. Returns a best-effort approximation from data
    that already exists today, so the Step 1/Step 2 screen has something
    real to render and test against in the meantime: reuses a job's own
    already-drafted resume_clarifying_questions if one exists (real
    questions, just missing the real point-value math), or an honest "not
    available yet" message if none does. point_value here is a naive
    equal split across open skill_gap questions - NOT the real 0.75/0.25
    required/preferred arithmetic - deliberately not trying to reproduce
    that math independently (see the message to ATS Engine, 2026-08-08:
    "not something I re-derive independently")."""
    from tailoring.applications import get_application

    app_record = get_application(job.get("source"), job.get("job_id")) or {}
    current_score = app_record.get("resume_ats_score")
    clarifying_questions = app_record.get("resume_clarifying_questions") or []

    skill_gap_count = sum(1 for q in clarifying_questions if q.get("type") != "disqualifier_check")
    remaining_points = max(0, 100 - (current_score or 0))
    per_question_points = round(remaining_points / skill_gap_count, 1) if skill_gap_count else None

    open_questions = []
    for q in clarifying_questions:
        is_disqualifier = q.get("type") == "disqualifier_check"
        open_questions.append({
            "type": q.get("type", "skill_gap"),
            "skill": q.get("skill"),
            "question": q.get("question"),
            "suggested_answer": q.get("suggested_answer") or "",
            "point_value": None if is_disqualifier else per_question_points,
        })

    return {
        "projected_score": current_score if current_score is not None else 0,
        "projected_rationale": app_record.get("resume_ats_rationale") or (
            "No resume has been drafted for this job yet - projected score "
            "isn't available until ATS Engine's baseline-selection work "
            "(item 1) lands." if current_score is None else ""
        ),
        "baseline_source": "this job's own current draft" if current_score is not None else "not available yet (item 1, pending)",
        "plateau_note": None,
        "open_questions": open_questions,
        "answer_more_exhausted_message": (
            "No more real gaps found based on your current profile." if current_score is not None and not open_questions else None
        ),
    }


def check_regenerate_needs_confirmation(job: dict, profile: dict) -> dict:
    """See module docstring. Defaults to has_new_info=True with no cost
    figures - meaning today, every regenerate shows the lightweight
    non-blocking heads-up (no real numbers to show yet) rather than the
    blocking confirmation gate. This is the SAFER default while this stub
    is in place: item 6's entire point is that the blocking gate exists to
    prevent a no-new-info regenerate from silently risking a keyword
    regression - defaulting to "assume there's new info" until the real
    check exists means this stub can accidentally let a no-new-info
    regenerate through without the gate it's meant to require. Flagged
    loudly in the UI copy (ui/app.py's _confirm_regenerate_dialog), not
    silently assumed safe."""
    from tailoring.applications import get_application

    return {
        "has_new_info": True,
        "new_fact_count": None,
        "estimated_new_score": None,
        "cost_estimate": None,
        "last_generation_cost": None,
        "current_score": (get_application(job.get("source"), job.get("job_id")) or {}).get("resume_ats_score"),
    }
