"""Standalone test harness (2026-08-10, Zahir's ask via General) for whether
fit_score/document-generation quality holds up on a cheaper model than the
DEFAULT_MODEL (claude-opus-5) fit_score currently runs on - fit_score alone
is 81% of Panga's real AI spend ($79.73 of $98.63 total, 256 calls,
avg $0.31/call - no batching, no pre-filter, no evident prompt caching).
This script ONLY swaps the model for a live test call; it deliberately does
NOT build the pre-filter or caching layers General also flagged - narrowly
scoped per Zahir's explicit instruction.

NOT wired into the app - run manually, one model at a time:
    venv\\Scripts\\python.exe scripts\\fit_score_model_test.py --model claude-sonnet-5
    venv\\Scripts\\python.exe scripts\\fit_score_model_test.py --model claude-haiku-4-5-20251001

HARD CONSTRAINTS (Zahir's explicit requirements, relayed by General):
- Isolated to this worktree/branch only. Does not import or touch anything
  on the Results tab's resume-generation UI path (src/ui/app.py) - calls
  tailoring.drafting.score_job()/generate_documents() directly, the same
  functions the UI itself calls, with an explicit model= override.
- Never writes to real production data. Reads the real job records/profile
  read-only, but every job passed to score_job/generate_documents is a copy
  with a namespaced fake job_id (see _test_copy) so any cache write
  (update_job_ats_keywords/update_job_address, both real side effects of
  generate_documents's resume/cover_letter paths) can never collide with
  the real record even before the monkeypatch below - belt and suspenders,
  since a bug in either safeguard alone must not corrupt jobs.json.
  update_job_ats_keywords/update_job_address are ALSO monkeypatched to
  no-ops as the primary safety net.
- Spend cap: stops before starting a new job's calls once cumulative real
  spend logged since this run started (via cost_log.py - the same real,
  already-computed dollar figures Ops tab reads) reaches SPEND_CAP_USD.
  Checked before EVERY API-calling step, not just once per job, so a
  single expensive job can't blow through the cap unnoticed.

Writes one results file per run: fit_score_model_test_results_<model>.json,
{job_id: {fit_score, fit_rationale, resume_ats_score, resume_text,
cover_letter_text, exec_bio_text, leadership_summary_text, cost_usd}} -
same shape as the baseline snapshot plus fit_score/fit_rationale/cost_usd,
so compare_to_baseline.py (same directory) can diff the two directly.

Known gap in the acceptance bar itself, worth flagging back rather than
silently assuming: fit_score_regression_baseline.json has no fit_score
value to compare against (only resume_ats_score + document text) - these
are already-applied jobs, so there is no "baseline fit_score" to hold this
run to. This script still reports fit_score/fit_rationale for the record,
but compare_to_baseline.py cannot score-compare fit_score - only judge it
for internal sanity (a real number, coherent rationale, not wildly
divergent from the resume's own ATS-score story).
"""

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv  # noqa: E402

import cost_log  # noqa: E402
import profile.storage as profile_storage  # noqa: E402
import search.job_store as job_store  # noqa: E402
import security.file_lock as file_lock  # noqa: E402
from tailoring import drafting  # noqa: E402

BASELINE_PATH = Path(
    r"C:\Users\User\AppData\Local\Temp\claude\C--Users-User-Desktop-Myra"
    r"\00f5af07-6bc9-422e-85c6-9a15b6411995\scratchpad\fit_score_regression_baseline.json"
)

# This worktree has no data/ of its own (gitignored, like every worktree -
# see CLAUDE.md's "testing a worktree's own unmerged code" note) - every
# *_PATH constant below resolves relative to each module's own __file__, so
# left alone they'd point at nonexistent files inside this worktree instead
# of the real, encrypted production stores. Redirected explicitly to the
# real root checkout's data/ instead. The actual AES key itself comes from
# the OS keyring (security.crypto_store._get_or_create_key), not a
# per-checkout file, so decryption/re-encryption is identical either way -
# no key-mismatch risk from running this from a worktree.
REAL_ROOT = Path(r"C:\Users\User\Desktop\Myra\Panga")
# llm_client.py's own load_dotenv(PROJECT_ROOT / ".env") call resolves
# PROJECT_ROOT relative to ITS OWN __file__ (this worktree), so it loads
# nothing (no .env here, gitignored like every worktree) - is_configured()
# was accidentally False the whole way through until this was added.
# Loaded explicitly here from the real root instead, same reasoning as
# every other real-data-path override in this file.
load_dotenv(REAL_ROOT / ".env")
job_store.JOBS_PATH = REAL_ROOT / "data" / "jobs" / "jobs.json"
profile_storage.MASTER_PROFILE_PATH = REAL_ROOT / "data" / "profile" / "structured" / "master_profile.json"
cost_log.COST_LOG_PATH = REAL_ROOT / "data" / "cost_log.json"
# Real race-condition risk (CLAUDE.md's own standing rule) if left
# unpatched: file_lock._LOCK_DIR also resolves relative to THIS worktree,
# so a lock acquired here would use a different physical lock file than
# the one the real Streamlit app/scheduled tasks use on the SAME real
# cost_log.json - genuinely non-exclusive despite both sides believing
# they're holding "the" lock. Pointed at the real lock directory so this
# script's cost_log writes are actually serialized against any concurrent
# real writer, not just against itself.
file_lock._LOCK_DIR = REAL_ROOT / "data" / ".locks"
RESULTS_DIR = Path(__file__).resolve().parent
SPEND_CAP_USD = 15.0
TEST_SOURCE = "linkedin"
TEST_JOB_IDS = ["4449005464", "4439921487", "4445190785", "4448567117"]
DOC_KEYS = ["resume", "cover_letter", "exec_bio", "leadership_summary"]


def _log(message: str) -> None:
    print(message, flush=True)


def _spend_since(start_iso: str) -> float:
    return sum(
        e.get("cost_usd", 0.0) for e in cost_log.load_cost_log()
        if e.get("timestamp", "") > start_iso
    )


def _load_real_job(job_id: str) -> dict:
    for job in job_store.load_jobs():
        if job.get("source") == TEST_SOURCE and job.get("job_id") == job_id:
            return job
    raise SystemExit(f"Job {TEST_SOURCE}/{job_id} not found in real jobs.json - check the ID.")


def _test_copy(real_job: dict, model_label: str) -> dict:
    test_job = dict(real_job)
    # Namespaced fake ID - see module docstring's "never writes to real
    # production data" constraint. Safe even without the monkeypatch below.
    test_job["job_id"] = f"TEST-{model_label}-{real_job['job_id']}"
    return test_job


def run(model: str) -> dict:
    if not drafting.is_configured():
        _log("No ANTHROPIC_API_KEY configured in this environment - cannot run.")
        return {}

    # Primary safety net (module docstring) - belt-and-suspenders alongside
    # the fake job_id above. Neither of generate_documents's two cache
    # writes (resume-path keyword extraction, cover_letter-path address
    # lookup) may touch real data/jobs/jobs.json during this test.
    job_store.update_job_ats_keywords = lambda *a, **kw: None
    job_store.update_job_address = lambda *a, **kw: None

    profile = profile_storage.load_profile()
    start_iso = cost_log.load_cost_log()[-1]["timestamp"] if cost_log.load_cost_log() else ""

    results = {}
    for job_id in TEST_JOB_IDS:
        spent = _spend_since(start_iso)
        if spent >= SPEND_CAP_USD:
            _log(f"STOPPING before job {job_id}: spend cap (${SPEND_CAP_USD:.2f}) reached at ${spent:.2f}.")
            break

        real_job = _load_real_job(job_id)
        test_job = _test_copy(real_job, model)
        _log(f"[{job_id}] scoring fit + drafting documents with model={model} (spend so far: ${spent:.2f})...")

        try:
            fit = drafting.score_job(test_job, profile, model=model)
            docs = drafting.generate_documents(test_job, profile, DOC_KEYS, model=model)
        except (drafting.DraftingNotConfigured, drafting.DraftingFailed) as exc:
            _log(f"  [FAILED] {exc}")
            results[job_id] = {"error": str(exc)}
            continue

        resume = docs.get("resume", {})
        results[job_id] = {
            "fit_score": fit.get("fit_score"),
            "fit_rationale": fit.get("fit_rationale"),
            "resume_ats_score": resume.get("ats_score"),
            "resume_text": resume.get("text"),
            "cover_letter_text": docs.get("cover_letter"),
            "exec_bio_text": docs.get("exec_bio"),
            "leadership_summary_text": docs.get("leadership_summary"),
            "cost_usd_running_total": _spend_since(start_iso),
        }
        _log(f"  fit_score={fit.get('fit_score')} resume_ats_score={resume.get('ats_score')} "
             f"running spend=${_spend_since(start_iso):.2f}")

    out_path = RESULTS_DIR / f"fit_score_model_test_results_{model}.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    _log(f"Results written to {out_path}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="e.g. claude-sonnet-5 or claude-haiku-4-5-20251001")
    args = parser.parse_args()
    run(args.model)
