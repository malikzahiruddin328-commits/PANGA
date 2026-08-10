"""Real perf bug (Zahir, live, 2026-08-10 - "the page is getting heavier
and heavier"/"internal refreshes taking way too long"): application_status()
called get_application(), which called load_applications() - a full
disk-read + AES-GCM decrypt + json.loads of the ENTIRE applications store -
on every single call. Measured against real production data (2070 jobs, 48
applications) via an isolated profiling script mirroring the real filter
pipeline exactly: 378 load_applications() calls / 1.42s for just one pass
through the three hide-by-default filters, and that same pattern repeated
2-3x more per Results-tab rerun (nav-bar badge, checkbox-label hidden
counts, per-row Status column). Root cause: load_applications() was never
cached or batched - a classic N+1.

Fix: `applications`/`applications_by_key` loaded and indexed once at
module top level (ui/app.py, right after `jobs = load_jobs()`), reused by
application_status() and every per-job app_record lookup for the rest of
that rerun, rather than each one re-reading the store itself.

These tests prove the call count no longer scales with job count - not
just that behavior is unchanged (the full regression suite already
covers that)."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application

APP_PATH = "src/ui/app.py"


def _make_jobs(n):
    return [
        {
            "source": "Dice", "job_id": f"job{i}", "title": f"Director {i}",
            "organization": "Acme Corp", "location": "Remote",
            "description": "Requirements: Python.",
        }
        for i in range(n)
    ]


@pytest.fixture
def many_jobs_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    n = 60
    save_jobs(_make_jobs(n))
    for i in range(n):
        update_job_score("Dice", f"job{i}", 85, "Strong match.")
    # A handful of real applications too, so application_status() has
    # something real to look up for some jobs and None for others.
    for i in range(0, n, 5):
        upsert_application("Dice", f"job{i}", status="under review")
    return n


def test_load_applications_is_called_a_small_constant_number_of_times_not_once_per_job(many_jobs_app, monkeypatch):
    import tailoring.applications as applications_module

    n = many_jobs_app
    call_count = 0
    real_load_applications = applications_module.load_applications

    def counting_load_applications():
        nonlocal call_count
        call_count += 1
        return real_load_applications()

    monkeypatch.setattr(applications_module, "load_applications", counting_load_applications)

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    assert not at.exception
    # Before the fix this scaled with job count (up to ~3x per job across
    # the badge/hidden-count/per-row passes - hundreds of calls at n=60).
    # After the fix it's a small constant, independent of n: one at module
    # top level, plus at most a couple more from other tabs/features this
    # same rerun happens to touch (e.g. Profile Gaps' own count, CTA
    # matching) - nowhere close to scaling with job count.
    assert call_count <= 5, f"load_applications() called {call_count} times for {n} jobs - still scales with job count"


def test_application_status_reflects_real_stored_status(isolated_data, monkeypatch):
    # Correctness check alongside the perf test above - the fast index
    # path must still return the real answer, not just be fast. The
    # Results-tab channel table itself is canvas-rendered (a pre-existing,
    # documented AppTest limitation - its content isn't inspectable via
    # at.markdown/at.dataframe), so this checks the "Show N job(s) marked
    # 'applied'" checkbox label instead - its count comes directly from
    # `sum(1 for j in ranked if application_status(j) in APPLIED_STATUSES)`,
    # the exact code path this fix changed.
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([
        {"source": "Dice", "job_id": "applied1", "title": "Director", "organization": "Acme", "location": "Remote"},
        {"source": "Dice", "job_id": "applied2", "title": "VP", "organization": "Acme", "location": "Remote"},
        {"source": "Dice", "job_id": "untouched1", "title": "CTO", "organization": "Acme", "location": "Remote"},
    ])
    for job_id in ("applied1", "applied2", "untouched1"):
        update_job_score("Dice", job_id, 85, "Strong match.")
    upsert_application("Dice", "applied1", status="applied")
    upsert_application("Dice", "applied2", status="applied")

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    assert not at.exception
    applied_checkbox = next(c for c in at.checkbox if c.key == "results_show_applied")
    assert "Show 2 job(s) marked 'applied'" in applied_checkbox.label
