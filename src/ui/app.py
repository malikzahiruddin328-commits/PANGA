"""Build step 6: Streamlit results UI - results list + per-job actions + "run now" button.

Run with: venv/Scripts/streamlit.exe run src/ui/app.py

Only USAJOBS.gov can be searched directly from the "Run now" button, since
it's a plain HTTP API. ZipRecruiter/Dice/Indeed are MCP connector tools, not
reachable from a standalone script - they're searched daily by the
panga-daily-job-search scheduled task instead (see
docs/daily-job-search-task.md), not this button.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pandas as pd
import streamlit as st
import yaml

from search.usajobs import search_jobs, USAJobsNotConfigured
from search.job_store import load_jobs
from ranking.prioritize import weight_for
from tailoring.applications import load_applications, upsert_application, get_application, get_pending_status_suggestions, confirm_status_suggestion
from tailoring.cta_emails import get_active_cta_emails, request_archive, request_draft, get_awaiting_draft_send

CATEGORY_LABELS = {
    "rejection": "Rejection",
    "interview_request": "Interview request",
    "assessment_request": "Assessment / take-home task",
    "offer": "Offer",
    "recruiter_question": "Recruiter question",
}
# Order sections appear in on the Call to Action page - lead with the ones
# that gain from a fast reply (offers, interviews), rejections last since
# they only need a short acknowledgment, not urgency.
CATEGORY_ORDER = ["offer", "interview_request", "assessment_request", "recruiter_question", "rejection"]

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

cta_count = len(get_active_cta_emails())
cta_page_label = f"Call to Action ({cta_count})" if cta_count else "Call to Action"
page = st.sidebar.radio("View", ["Results", cta_page_label, "Settings"])

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

elif page.startswith("Call to Action"):
    st.header("Call to Action")
    st.caption("Emails the Gmail scan flagged as needing a reply or a decision.")

    r1, r2 = st.columns([1, 5])
    with r1:
        if st.button("Refresh"):
            st.rerun()

    # A background task (panga-cta-fulfillment, runs every ~10 min while the
    # app is open) does the actual Gmail work behind Dismiss/Draft reply, and
    # checks whether drafts it created have since been sent. Refresh just
    # re-reads that outcome from disk - it can't reach Gmail live itself
    # (Streamlit has no MCP/Claude access) - so this always shows the picture
    # as of the last fulfillment run, not the literal instant you click.
    outstanding_drafts = get_awaiting_draft_send()
    if outstanding_drafts:
        st.info(f"{len(outstanding_drafts)} draft(s) created and waiting in Gmail for you to review and send. This count updates automatically once you send them - no need to come back and dismiss them yourself.")
    else:
        st.caption("No drafts outstanding - everything's either dismissed or sent.")

    all_cta = get_active_cta_emails()

    counts = {cat: sum(1 for e in all_cta if e.get("category") == cat) for cat in CATEGORY_ORDER}
    stat_cols = st.columns(len(CATEGORY_ORDER))
    for col, cat in zip(stat_cols, CATEGORY_ORDER):
        with col:
            st.metric(CATEGORY_LABELS[cat], counts[cat])

    f1, f2 = st.columns([1, 2])
    with f1:
        category_filter = st.selectbox("Category", ["All categories"] + [CATEGORY_LABELS[c] for c in CATEGORY_ORDER])
    with f2:
        search_text = st.text_input("Search subject or sender", "")

    filtered = all_cta
    if category_filter != "All categories":
        filtered = [e for e in filtered if CATEGORY_LABELS.get(e.get("category")) == category_filter]
    if search_text.strip():
        needle = search_text.strip().lower()
        filtered = [e for e in filtered if needle in (e.get("subject") or "").lower() or needle in (e.get("sender") or "").lower()]

    if not all_cta:
        st.caption("Nothing needs attention right now.")
    elif not filtered:
        st.caption("No matches for that filter/search.")

    for cat in CATEGORY_ORDER:
        cat_emails = [e for e in filtered if e.get("category") == cat]
        if not cat_emails:
            continue
        st.subheader(CATEGORY_LABELS[cat])
        for email in cat_emails:
            st.markdown(f"**{email.get('subject')}**")
            st.caption(f"{email.get('sender')} - {email.get('date')}")
            if email.get("snippet"):
                st.caption(email["snippet"])

            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                st.link_button("Open in Gmail", email["gmail_link"], key=f"open_cta_{email['thread_id']}")
            with c2:
                if email.get("draft_created"):
                    st.link_button("Open draft", email["draft_link"], key=f"draft_link_{email['thread_id']}")
                elif email.get("draft_requested"):
                    st.button("Draft requested...", key=f"draft_pending_{email['thread_id']}", disabled=True)
                else:
                    if st.button("Draft reply", key=f"draft_cta_{email['thread_id']}"):
                        request_draft(email["thread_id"])
                        st.success("Draft requested - will be created in Gmail on the next scan run.")
                        st.rerun()
            with c3:
                if st.button("Dismiss", key=f"dismiss_cta_{email['thread_id']}"):
                    request_archive(email["thread_id"])
                    st.rerun()
            st.divider()

elif page == "Results":
    settings = load_settings()

    pending_suggestions = get_pending_status_suggestions()
    if pending_suggestions:
        st.warning(f"{len(pending_suggestions)} application match(es) found from your inbox - confirm or dismiss below.")
        for sugg in pending_suggestions:
            job = next((j for j in load_jobs() if j["source"] == sugg["source"] and j["job_id"] == sugg["job_id"]), None)
            job_label = job["title"] if job else f"{sugg['source']} {sugg['job_id']}"
            st.markdown(f"**{job_label}** -> mark as \"{sugg['suggested_status']}\"?")
            st.caption(sugg.get("suggested_status_reason") or "")
            s1, s2 = st.columns(2)
            with s1:
                if st.button("Confirm", key=f"confirm_{sugg['source']}_{sugg['job_id']}"):
                    confirm_status_suggestion(sugg["source"], sugg["job_id"], accept=True)
                    st.rerun()
            with s2:
                if st.button("Dismiss", key=f"dismiss_{sugg['source']}_{sugg['job_id']}"):
                    confirm_status_suggestion(sugg["source"], sugg["job_id"], accept=False)
                    st.rerun()

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
        st.caption("This button only covers USAJOBS.gov directly. ZipRecruiter, Dice, and Indeed are searched automatically once a day by the scheduled task instead (they're MCP connector tools, not reachable from this button).")

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

    # Grouped by channel (source) per Zahir's request 2026-07-29 - each
    # channel (USAJOBS, Dice, ZipRecruiter, etc.) gets its own section.
    # Dedup only happens WITHIN a channel (job_store keys on source+job_id) -
    # the same real job posted on two different channels shows once per
    # channel, since they're technically distinct postings (different links,
    # sometimes different pay listed), not auto-merged.
    channels = list(dict.fromkeys(j["source"] for j in ranked))  # first-appearance order, dedup'd

    def dedupe_key(job):
        # Same title+org+location+pay within a channel = almost certainly the
        # same real job posted under two announcements (e.g. merit-promotion
        # + open-competitive on USAJOBS) - confirmed 2026-07-29 with the two
        # identical "Audit Director (IT)" postings. Cross-channel matching
        # isn't attempted here - different platforms format titles/orgs too
        # differently for exact matching to be reliable, and the risk of
        # wrongly merging two different jobs is higher than the clutter cost.
        return (job.get("title"), job.get("organization"), job.get("location"), job.get("pay_min"), job.get("pay_max"))

    for channel in channels:
        channel_jobs = [j for j in ranked if j["source"] == channel]

        groups = {}
        for job in channel_jobs:
            groups.setdefault(dedupe_key(job), []).append(job)
        deduped = [postings[0] for postings in groups.values()]  # first (highest-ranked) as primary
        postings_by_primary = {id(postings[0]): postings for postings in groups.values()}

        dup_note = f", {len(channel_jobs) - len(deduped)} duplicate posting(s) merged" if len(deduped) < len(channel_jobs) else ""
        with st.expander(f"{channel} ({len(deduped)}{dup_note})", expanded=True):
            table_rows = []
            for job in deduped:
                pay = f"${job.get('pay_min') or '?'}-{job.get('pay_max') or '?'}" if (job.get("pay_min") or job.get("pay_max")) else (job.get("salary_text") or "")
                table_rows.append({
                    "Role": job.get("title"),
                    "Organization": job.get("organization"),
                    "Pay": pay,
                    "Score": job.get("fit_score"),
                    "Status": application_status(job) or "-",
                    "Posting": job.get("posting_url"),
                })
            df = pd.DataFrame(table_rows)

            event = st.dataframe(
                df,
                hide_index=True,
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={"Posting": st.column_config.LinkColumn(display_text="Open")},
                key=f"table_{channel}",
            )

            selected_rows = event.selection.rows if event and event.selection else []
            if selected_rows:
                job = deduped[selected_rows[0]]
                postings = postings_by_primary[id(job)]

                st.caption(f"{job.get('location') or 'Location not listed'}")
                if "fit_score" in job:
                    st.caption(job.get("fit_rationale") or "")
                else:
                    st.caption("Compatibility: not yet scored")
                if len(postings) > 1:
                    for i, posting in enumerate(postings, start=1):
                        if posting.get("posting_url"):
                            st.link_button(f"Open posting ({i} of {len(postings)})", posting["posting_url"], key=f"open_{posting.get('source')}_{posting.get('job_id')}")

                b2, b3 = st.columns(2)
                with b2:
                    if st.button("Start tailoring", key=f"tailor_{job.get('source')}_{job.get('job_id')}"):
                        upsert_application(job["source"], job["job_id"], status="under review")
                        st.info("Marked \"under review.\" Go to Claude Code and ask to tailor this job - that's where the actual resume/cover letter drafting happens (per the PRD's LLM architecture, not inside this app). Once you've actually submitted it, come back and mark it \"applied.\"")
                        st.rerun()
                with b3:
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
