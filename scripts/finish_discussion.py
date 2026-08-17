"""CLI entry point (2026-08-13) for a live Claude Code session to trigger
the "Discuss & draft" basket operation's phase 2 - see tailoring.
discuss_and_draft.finish_discussion() for the real logic; this script is
just a thin, non-Streamlit way to call it directly from a conversation,
mirroring how scripts/_zahir_catalent_add_and_generate.py called
generate_documents() directly for the manual precedent this feature
replaces.

Only run this AFTER the job's open questions (posted to the shared
message board by "Discuss & draft" in the basket UI) have actually been
resolved in a live conversation with Zahir AND
tailoring.drafting.save_gap_answers() has already been called with his
real, confirmed answers - this script does not ask any questions itself,
it only fires the one final draft call.

Usage:
    venv/Scripts/python.exe scripts/finish_discussion.py <source> <job_id> [doc_key ...]

Defaults to ["resume", "cover_letter"] if no doc_keys are given, matching
the basket UI's own default selection.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from search.job_store import load_jobs
from tailoring.discuss_and_draft import finish_discussion
from profile.storage import load_profile


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    source, job_id = sys.argv[1], sys.argv[2]
    doc_keys = sys.argv[3:] or ["resume", "cover_letter"]

    job = next((j for j in load_jobs() if j.get("source") == source and j.get("job_id") == job_id), None)
    if job is None:
        print(f"No job found for source={source!r} job_id={job_id!r}.")
        return 1

    profile = load_profile()
    print(f"Finishing discussion for {job.get('title')} at {job.get('organization')} ({source}/{job_id}) - doc_keys={doc_keys}")
    result = finish_discussion(job, profile, doc_keys)

    if result.get("locked"):
        print("Could not acquire the generation lock - another draft is already in progress for this job. Try again shortly.")
        return 1
    if result["ok"]:
        print("Done - final draft persisted.")
        return 0
    print(f"Drafting finished with errors: {result['errors']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
