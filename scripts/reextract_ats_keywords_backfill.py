"""One-off backfill (2026-08-09): forces a fresh AI re-extraction of
ats_required_keywords/ats_preferred_keywords for every already-scored job,
and re-scores each job's already-drafted resume (if any) against the
corrected list - WITHOUT redrafting the resume text itself (CLAUDE.md known
failure pattern #2: a full redraft is its own separate regression risk this
doesn't need to take).

Why this exists: generate_documents() only ever calls _extract_ats_keywords()
once per job, cached for good on the assumption a posting's own text doesn't
change (see that function's docstring in tailoring/drafting.py). That's the
right call for repeat regenerates of the SAME job, but it also means every
job scored BEFORE 2026-08-09's either/or keyword-extraction generalization
(N-tier degree-field chains, silently-dropped broad-sounding alternatives
like "Business", dual-role terms) is stuck on its old, possibly-buggy
keyword list forever - the fix is real but inert for jobs that predate it.
Real example that surfaced this: Zahir's Upstream Bio job stayed stuck below
90 because its keywords were extracted before the fix, even after the fix
shipped and even after a resume regenerate (which only redrafts, it doesn't
re-extract).

This makes one real Anthropic API call per already-scored job - run it
deliberately, not on a schedule, since it has a real (small) cost and Zahir
should decide when that's worth spending, not have it fire automatically.

Run once: venv\\Scripts\\python.exe scripts\\reextract_ats_keywords_backfill.py
Safe to re-run - each run is a fresh, independent re-extraction; running it
twice in a row just spends the API cost twice for (most likely) the same
result, it won't corrupt anything.
"""

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tailoring import applications, drafting  # noqa: E402
from tailoring.ats_score import score_resume_against_keywords  # noqa: E402
from search import job_store  # noqa: E402


def _log(message: str) -> None:
    print(message, flush=True)


def backfill() -> tuple[int, int, int]:
    """Returns (reextracted_count, rescored_count, failed_count)."""
    if not drafting.is_configured():
        _log("No ANTHROPIC_API_KEY configured in this environment - cannot run.")
        return 0, 0, 0

    client = drafting._client()
    jobs = job_store.load_jobs()
    apps_by_key = {(a["source"], a["job_id"]): a for a in applications.load_applications()}

    reextracted = 0
    rescored = 0
    failed = 0

    for job in jobs:
        if job.get("ats_required_keywords") is None:
            # Never extracted at all - generate_documents()'s existing gate
            # already does this correctly on this job's next real draft, no
            # backfill needed.
            continue
        source, job_id = job.get("source"), job.get("job_id")
        old_required = job.get("ats_required_keywords")
        old_preferred = job.get("ats_preferred_keywords")

        required, preferred = drafting._extract_ats_keywords(client, job, model=None)
        changed = job.get("ats_required_keywords") is not old_required or job.get("ats_preferred_keywords") is not old_preferred
        if not changed:
            failed += 1
            _log(f"  [failed] {source} / {job.get('title')!r} ({job_id}) - re-extraction call did not succeed, keywords unchanged")
            continue
        reextracted += 1
        _log(f"  [reextracted] {source} / {job.get('title')!r} ({job_id})")

        app_record = apps_by_key.get((source, job_id))
        resume_text = (app_record or {}).get("resume_text")
        if app_record and resume_text:
            # Only refreshes score/rationale/next_actions - not
            # clarifying_questions, which needs profile-aware skill
            # matching (see _merge_keyword_gap_questions in drafting.py);
            # out of scope for a pure keyword-cache refresh. The Results
            # tab's "Re-check keywords" button (reextract_ats_keywords_
            # and_rescore) does the full merge for a single job on demand.
            ats = score_resume_against_keywords(required, preferred, resume_text)
            applications.upsert_application(
                source, job_id, status=app_record.get("status", "under review"),
                resume_ats_score=ats["ats_score"],
                resume_ats_rationale=ats["ats_rationale"],
                resume_ats_next_actions=ats["ats_next_actions"],
            )
            rescored += 1
            _log(f"    [rescored] new ATS score {ats['ats_score']}/100 (was {app_record.get('resume_ats_score')})")

        # Small pause between real API calls - not a rate-limit workaround
        # (no evidence of throttling), just deliberate pacing since this can
        # run over every stored job in one shot.
        time.sleep(0.5)

    return reextracted, rescored, failed


if __name__ == "__main__":
    reextracted, rescored, failed = backfill()
    _log(f"Re-extracted keywords for {reextracted} job(s), rescored {rescored} drafted resume(s), {failed} re-extraction call(s) failed.")
