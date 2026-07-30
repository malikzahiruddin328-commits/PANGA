"""Per-application "dossier" (Zahir's request 2026-07-30): one traceable
file per job actually engaged with (applied, rejected, under review, etc.),
consolidating the posting itself, application status/timeline, drafted
documents, and interview prep into a single place - instead of having to
cross-reference jobs.json/applications.json/interview_prep.json by hand for
context on one job. Markdown, not JSON: most of the content (drafted cover
letters, interview notes) is prose meant to be read, not machine-parsed -
nothing else in Panga reads this file back in, it's purely a readable
output.

Regenerated (overwritten wholesale, not appended/merged) from the
source-of-truth stores every time any of them changes for a given job - see
the write_dossier() call sites in applications.py/interview_prep.py. This
module only reads from those stores; it never writes back to them.

Encrypted at rest via security.crypto_store, same as every other file under
data/ (PRD §7) - no carve-out for this one, even though that means it isn't
directly double-clickable outside Panga. Flagged to Zahir as a tradeoff
worth revisiting if he wants a plain-text export option later.
"""

import hashlib
import re
from pathlib import Path

from search.job_store import load_jobs
from tailoring.applications import get_application
from tailoring.interview_prep import get_interview_prep
from security.crypto_store import write_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOSSIER_DIR = PROJECT_ROOT / "data" / "applications" / "dossiers"

DOC_LABELS = {
    "resume_text": "Resume",
    "cover_letter_text": "Cover letter",
    "exec_bio_text": "Executive bio",
    "leadership_summary_text": "Leadership summary",
}


def _slug(source: str, job_id: str, organization: str | None, title: str | None) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", f"{organization or ''}-{title or ''}".lower()).strip("-")[:60]
    short_hash = hashlib.sha1(f"{source}:{job_id}".encode()).hexdigest()[:8]
    return f"{base or 'job'}-{short_hash}"


def dossier_path(source: str, job_id: str, organization: str | None = None, title: str | None = None) -> Path:
    return DOSSIER_DIR / f"{_slug(source, job_id, organization, title)}.md"


def _format_pay(job: dict) -> str | None:
    if job.get("salary_text"):
        return job["salary_text"]
    if job.get("pay_min") or job.get("pay_max"):
        return f"${job.get('pay_min') or '?'}-${job.get('pay_max') or '?'}"
    return None


def write_dossier(source: str, job_id: str) -> Path | None:
    """Regenerates the dossier file for one job from current data. Returns
    None (writes nothing) if the job itself isn't in jobs.json - a dossier
    only makes sense once there's a real posting to attach it to."""
    job = next((j for j in load_jobs() if j.get("source") == source and j.get("job_id") == job_id), None)
    if job is None:
        return None
    application = get_application(source, job_id) or {}
    prep = get_interview_prep(source, job_id)

    lines = [
        f"# {job.get('title')} - {job.get('organization')}",
        "",
        f"- **Source:** {source}",
        f"- **Posting URL:** {job.get('posting_url') or 'n/a'}",
        f"- **Location:** {job.get('location') or 'not listed'}",
    ]
    pay = _format_pay(job)
    if pay:
        lines.append(f"- **Pay:** {pay}")
    if "fit_score" in job:
        lines.append(f"- **Compatibility score:** {job['fit_score']}/100")
        if job.get("fit_rationale"):
            lines.append(f"- **Why:** {job['fit_rationale']}")
    lines.append(f"- **Status:** {application.get('status') or 'not yet tracked'}")
    if application.get("strategy_tag"):
        lines.append(f"- **Strategy tag:** {application['strategy_tag']}")
    if application.get("skip_reason"):
        lines.append(f"- **Skip reason:** {application['skip_reason']}")
    if application.get("created_at"):
        lines.append(f"- **Application started:** {application['created_at']}")
    if application.get("status_updated_at"):
        lines.append(f"- **Last status update:** {application['status_updated_at']}")
    lines.append("")

    if any(application.get(field) for field in DOC_LABELS):
        lines.append("## Documents")
        lines.append("")
        for field, label in DOC_LABELS.items():
            text = application.get(field)
            if text:
                lines.append(f"### {label}")
                lines.append("")
                lines.append(text)
                lines.append("")

    if prep and prep.get("rounds"):
        lines.append("## Interview prep")
        lines.append("")
        for round_ in prep["rounds"]:
            status_note = round_.get("outcome") or round_.get("status") or "in progress"
            lines.append(f"### {round_['round_label']} - {status_note}")
            logistics = " - ".join(v for v in [round_.get("date"), round_.get("format")] if v)
            if logistics:
                lines.append(f"*{logistics}*")
            lines.append("")
            for person in round_.get("interviewers") or []:
                title_part = f", {person['title']}" if person.get("title") else ""
                lines.append(f"**{person.get('name')}{title_part}**")
                if person.get("research_summary"):
                    lines.append(person["research_summary"])
                if person.get("persona"):
                    lines.append(f"_Likely focus:_ {person['persona']}")
                lines.append("")
            if round_.get("company_snapshot"):
                lines.append(f"**Company snapshot:** {round_['company_snapshot']}")
                lines.append("")
            if round_.get("likely_questions"):
                lines.append("**Likely questions:**")
                for q in round_["likely_questions"]:
                    asked_by = f" ({q['asked_by']})" if q.get("asked_by") else ""
                    lines.append(f"- {q.get('question')}{asked_by}")
                    if q.get("why"):
                        lines.append(f"  - why: {q['why']}")
                    if q.get("talking_point"):
                        lines.append(f"  - talking point: {q['talking_point']}")
                lines.append("")
            if round_.get("questions_to_ask"):
                lines.append("**Questions to ask them:**")
                for q in round_["questions_to_ask"]:
                    best_for = f" (best for {q['best_for']})" if q.get("best_for") else ""
                    lines.append(f"- {q.get('question')}{best_for}")
                lines.append("")
            if round_.get("outcome_notes"):
                lines.append(f"**Notes on how it went:** {round_['outcome_notes']}")
                lines.append("")

    path = dossier_path(source, job_id, job.get("organization"), job.get("title"))
    write_text(path, "\n".join(lines))
    return path
