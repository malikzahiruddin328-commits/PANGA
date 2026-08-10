"""Compares fit_score_model_test.py's results against
fit_score_regression_baseline.json for Zahir's explicit, non-negotiable
acceptance bar (relayed by General, 2026-08-10): the cheaper model must
match or beat both the ATS score AND the actual document content (same
real facts, no dropped claims, no wording regressions) versus the
claude-opus-5 baseline on these 4 real applied jobs. "A passing number
alone isn't enough" - so this does two independent checks per job, not one:

1. Deterministic, automated: resume_ats_score >= baseline, and zero
   keywords lost per tailoring.ats_score.detect_matched_keyword_regressions
   (the exact tool built 2026-08-09 for "did a regenerate silently drop a
   previously-matched keyword" - reused here rather than re-derived, same
   real arithmetic against the job's own cached ats_required_keywords/
   ats_preferred_keywords).
2. Human-judgable, NOT automated: a unified diff of each document's full
   text is written to disk. Whether a diff represents a genuine dropped
   claim or wording regression (vs. an equally-valid rephrasing) is
   exactly the kind of fuzzy judgment call this app's own scoring
   philosophy (ats_score.py's module docstring, CLAUDE.md known failure
   pattern #3) refuses to automate with a heuristic - a human (or a
   second AI read, but not this script pretending to be one) has to
   actually read the diff for the "no dropped claims" half of the bar.

Also reports fit_score against a real historical baseline pulled from
fit_score_baseline_supplement.json (2026-08-10) - General's first pointer
(cost_log.json entries keyed by job_id/purpose='fit_score') turned out not
to hold for these 4 specific jobs (zero matching entries, and cost_log
entries never stored the score value itself anyway - only cost/token
metadata) - the real historical fit_score/fit_rationale for each of the 4
jobs actually lives on the job record itself (job["fit_score"]/
job["fit_rationale"] in jobs.json), confirmed directly against the real
file and captured into the supplement alongside this commit. Reported
informationally (not gating REVIEW_NEEDED the way ats_score/keyword-loss
do) since these are already-applied jobs - fit_score no longer drives a
live apply/skip decision for them, so a difference here is a data point
for judging the cheaper model's scoring judgment, not a functional
regression the way a dropped resume keyword would be.

Run after fit_score_model_test.py for a given model:
    venv\\Scripts\\python.exe scripts\\compare_to_baseline.py --model claude-sonnet-5
"""

import argparse
import difflib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import search.job_store as job_store  # noqa: E402
from tailoring.ats_score import detect_matched_keyword_regressions  # noqa: E402

FIT_SCORE_BASELINE_PATH = Path(__file__).resolve().parent / "fit_score_baseline_supplement.json"
BASELINE_PATH = Path(
    r"C:\Users\User\AppData\Local\Temp\claude\C--Users-User-Desktop-Myra"
    r"\00f5af07-6bc9-422e-85c6-9a15b6411995\scratchpad\fit_score_regression_baseline.json"
)
RESULTS_DIR = Path(__file__).resolve().parent
REAL_ROOT = Path(r"C:\Users\User\Desktop\Myra\Panga")
TEXT_FIELDS = ["resume_text", "cover_letter_text", "exec_bio_text", "leadership_summary_text"]


def _real_job_keywords(job_id: str) -> tuple[list, list]:
    job_store.JOBS_PATH = REAL_ROOT / "data" / "jobs" / "jobs.json"
    for job in job_store.load_jobs():
        if job.get("source") == "linkedin" and job.get("job_id") == job_id:
            return job.get("ats_required_keywords") or [], job.get("ats_preferred_keywords") or []
    return [], []


def _write_diff(job_id: str, field: str, old_text: str, new_text: str, out_dir: Path) -> bool:
    """Returns True if there's any real diff to review."""
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = (new_text or "").splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=f"baseline/{field}", tofile=f"new/{field}"))
    if not diff:
        return False
    out_path = out_dir / f"{job_id}_{field}.diff"
    out_path.write_text("".join(diff), encoding="utf-8")
    return True


def compare(model: str) -> dict:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    fit_score_baseline = (
        json.loads(FIT_SCORE_BASELINE_PATH.read_text(encoding="utf-8"))
        if FIT_SCORE_BASELINE_PATH.exists() else {}
    )
    results_path = RESULTS_DIR / f"fit_score_model_test_results_{model}.json"
    if not results_path.exists():
        raise SystemExit(f"No results file at {results_path} - run fit_score_model_test.py --model {model} first.")
    new_results = json.loads(results_path.read_text(encoding="utf-8"))

    diff_dir = RESULTS_DIR / f"diffs_{model}"
    diff_dir.mkdir(exist_ok=True)

    summary = {}
    for job_id, base in baseline.items():
        new = new_results.get(job_id)
        if new is None or new.get("error"):
            summary[job_id] = {"status": "MISSING_OR_FAILED", "detail": (new or {}).get("error")}
            continue

        required, preferred = _real_job_keywords(job_id)
        lost_keywords = detect_matched_keyword_regressions(
            required, preferred, base.get("resume_text"), new.get("resume_text") or "",
        )

        base_ats = base.get("resume_ats_score")
        new_ats = new.get("resume_ats_score")
        ats_ok = new_ats is not None and base_ats is not None and new_ats >= base_ats

        base_fit = fit_score_baseline.get(job_id, {})

        diffs_written = []
        for field in TEXT_FIELDS:
            base_text = base.get(field)
            new_text = new.get(field)
            if base_text is None and new_text is None:
                continue
            if _write_diff(job_id, field, base_text, new_text, diff_dir):
                diffs_written.append(field)

        summary[job_id] = {
            "status": "PASS_AUTOMATED_CHECKS" if (ats_ok and not lost_keywords) else "REVIEW_NEEDED",
            "baseline_ats_score": base_ats,
            "new_ats_score": new_ats,
            "ats_score_ok": ats_ok,
            # Informational only - not a gating check, see module docstring
            # (these are already-applied jobs; fit_score no longer drives a
            # live decision for them).
            "baseline_fit_score": base_fit.get("fit_score"),
            "baseline_fit_rationale": base_fit.get("fit_rationale"),
            "new_fit_score": new.get("fit_score"),
            "new_fit_rationale": new.get("fit_rationale"),
            "lost_keywords": lost_keywords,
            "fields_with_diffs_to_review": diffs_written,
        }

    print(json.dumps(summary, indent=2))
    print(f"\nPer-field diffs (for the manual 'no dropped claims/no wording regressions' read) written under {diff_dir}")
    print("Automated checks alone are NOT the acceptance bar - Zahir's requirement is explicit that a passing "
          "number isn't enough; read every .diff file before calling a job's documents a pass.")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    compare(args.model)
