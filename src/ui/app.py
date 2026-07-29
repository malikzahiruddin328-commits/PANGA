"""Build step 6: Streamlit results UI - results list + per-job actions + "run now" button.

Run with: venv/Scripts/streamlit.exe run src/ui/app.py

Only USAJOBS.gov can be searched directly from this button, since it's a
plain HTTP API. ZipRecruiter/Dice/Gmail results only appear here after
Claude adds them during a live session (they're MCP connector tools, not
reachable from a standalone script) - see docs/email-monitoring-task.md and
the note in search/boards.py for why.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import streamlit as st
import yaml

from search.usajobs import search_jobs, USAJobsNotConfigured
from search.job_store import load_jobs
from ranking.prioritize import weight_for
from tailoring.applications import load_applications, upsert_application, get_application

SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

st.set_page_config(page_title="Panga - Job Search", layout="wide")


def load_settings() -> dict:
    return yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")


def application_status(job: dict) -> str | None:
    app = get_application(job.get("source"), job.get("job_id"))
    return app["status"] if app else None


st.title("Panga - Job Search")

page = st.sidebar.radio("View", ["Results", "Settings"])

if page == "Settings":
    st.header("Target roles and industries")
    st.caption("These control sort order on the Results screen - higher weight surfaces first. Not a search filter; every source is still searched the same way.")

    settings = load_settings()

    st.subheader("Target roles")
    roles_df = st.data_editor(
        settings.get("target_roles", []),
        num_rows="dynamic",
        column_config={
            "name": st.column_config.TextColumn("Role name", required=True),
            "priority_weight": st.column_config.NumberColumn("Priority weight", required=True, min_value=0, max_value=10),
        },
        key="roles_editor",
    )

    st.subheader("Industries")
    industries_text = st.text_area(
        "One per line",
        value="\n".join(settings.get("industries", [])),
    )

    st.subheader("USAJOBS job series")
    st.caption("See \"Occupations and job series\" on usajobs.gov for codes. This runs alongside keyword search (not instead of it) - government classification is inconsistent, so restricting to one series alone would miss real matches filed under a different code. The compatibility score, not this filter, is what actually screens for relevance.")
    job_series_text = st.text_area(
        "One code per line, e.g. 2210 for Information Technology Management",
        value="\n".join(settings.get("usajobs_job_series", [])),
    )

    if st.button("Save settings"):
        settings["target_roles"] = roles_df
        settings["industries"] = [line.strip() for line in industries_text.splitlines() if line.strip()]
        settings["usajobs_job_series"] = [line.strip() for line in job_series_text.splitlines() if line.strip()]
        save_settings(settings)
        st.success("Saved.")

else:
    settings = load_settings()

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("Run now (USAJOBS)", type="primary"):
            with st.spinner("Searching USAJOBS.gov..."):
                try:
                    from search.job_store import save_jobs
                    total_new = 0
                    # Keyword search per target role, not restricted to any one
                    # job category - USAJOBS classification is inconsistent
                    # relative to actual job content (e.g. "Audit Director (IT)"
                    # and "Head of Innovation" are filed as Auditing/Program
                    # Management, not IT Management), so a hard category filter
                    # would silently exclude good matches. The compatibility
                    # score, not the search itself, is what filters for quality.
                    for role in settings.get("target_roles", []):
                        results = search_jobs(keyword=role["name"], results_per_page=50)
                        total_new += save_jobs(results)
                    # Job series search as a supplementary net, for roles a
                    # role-name keyword wouldn't catch (e.g. "IT Specialist (AI)").
                    for code in settings.get("usajobs_job_series", []):
                        results = search_jobs(job_category_code=code, results_per_page=100)
                        total_new += save_jobs(results)
                    st.success(f"Found {total_new} new job(s).")
                except USAJobsNotConfigured as e:
                    st.error(str(e))
    with col2:
        st.caption("ZipRecruiter, Dice, and other connector-based sources only update here when Claude runs a search during a session - this button only covers USAJOBS.gov directly.")

    jobs = load_jobs()
    target_roles = settings.get("target_roles", [])

    def sort_key(job):
        has_score = "fit_score" in job
        return (has_score, job.get("fit_score", -1), weight_for(job.get("title"), target_roles))

    ranked = sorted(jobs, key=sort_key, reverse=True)

    unscored_count = sum(1 for j in jobs if "fit_score" not in j)
    scored_count = len(jobs) - unscored_count
    min_score = st.slider(
        "Minimum compatibility score",
        0, 100, 30,
        help="Hides low-fit results (e.g. unrelated roles pulled in by broad keyword matches).",
    )
    # Unscored jobs are hidden by default, NOT always shown - showing
    # unscored jobs regardless of the slider defeated the purpose of scoring
    # (e.g. a fresh "Run now" search returning an unscored Physician role
    # would display no matter how high the threshold was set). Scoring only
    # happens via the daily scheduled task or a manual Claude pass, so new
    # jobs from "Run now" won't show here until one of those has run.
    ranked = [j for j in ranked if "fit_score" in j and j["fit_score"] >= min_score]

    NOT_INTERESTED = ("not interested", "not-interested")  # handle both forms - see applications.py note
    not_interested_count = sum(1 for j in ranked if application_status(j) in NOT_INTERESTED)
    show_not_interested = st.checkbox(f"Show {not_interested_count} job(s) marked 'not interested' (hidden by default, nothing is deleted)")
    if not show_not_interested:
        ranked = [j for j in ranked if application_status(j) not in NOT_INTERESTED]

    if unscored_count:
        st.caption(f"{unscored_count} job(s) found but not yet compatibility-scored - hidden until the next scoring pass (daily scheduled task, or ask Claude to score them now).")

    st.subheader(f"{len(ranked)} job(s)")

    for job in ranked:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**{job.get('title')}** - {job.get('organization')}")
                st.caption(f"{job.get('location') or 'Location not listed'} | Source: {job.get('source')}")
                if job.get("pay_min") or job.get("pay_max"):
                    st.caption(f"Pay: {job.get('pay_min') or '?'} - {job.get('pay_max') or '?'}")
                elif job.get("salary_text"):
                    st.caption(f"Pay: {job.get('salary_text')}")

                if "fit_score" in job:
                    st.markdown(f"**Compatibility: {job['fit_score']}/100**")
                    st.caption(job.get("fit_rationale") or "")
                else:
                    st.caption("Compatibility: not yet scored")

                status = application_status(job)
                if status:
                    st.caption(f"Status: {status}")

            with c2:
                if job.get("posting_url"):
                    st.link_button("Open posting", job["posting_url"])

                if st.button("Start tailoring", key=f"tailor_{job.get('source')}_{job.get('job_id')}"):
                    upsert_application(job["source"], job["job_id"], status="drafted")
                    st.info("Marked as drafted. Go to Claude Code and ask to tailor this job - that's where the actual resume/cover letter drafting happens (per the PRD's LLM architecture, not inside this app).")

                new_status = st.selectbox(
                    "Mark status",
                    ["-", "applied", "not interested", "save for later"],
                    key=f"status_{job.get('source')}_{job.get('job_id')}",
                )
                if new_status != "-":
                    skip_reason = None
                    if new_status == "not interested":
                        skip_reason = st.text_input("Why not interested? (optional)", key=f"reason_{job.get('source')}_{job.get('job_id')}")
                    if st.button("Save status", key=f"save_status_{job.get('source')}_{job.get('job_id')}"):
                        upsert_application(job["source"], job["job_id"], status=new_status, skip_reason=skip_reason)
                        st.success("Status saved.")
                        st.rerun()
