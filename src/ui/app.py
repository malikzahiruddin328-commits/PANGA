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
from ranking.prioritize import sort_by_priority
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

    if st.button("Save settings"):
        settings["target_roles"] = roles_df
        settings["industries"] = [line.strip() for line in industries_text.splitlines() if line.strip()]
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
                    for role in settings.get("target_roles", []):
                        results = search_jobs(keyword=role["name"], results_per_page=25)
                        total_new += save_jobs(results)
                    st.success(f"Found {total_new} new job(s).")
                except USAJobsNotConfigured as e:
                    st.error(str(e))
    with col2:
        st.caption("ZipRecruiter, Dice, and other connector-based sources only update here when Claude runs a search during a session - this button only covers USAJOBS.gov directly.")

    jobs = load_jobs()
    ranked = sort_by_priority(jobs, settings.get("target_roles", []))

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
