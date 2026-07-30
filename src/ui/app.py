"""Build step 6: Streamlit results UI - results list + per-job actions + "run now" button.

Run with: venv/Scripts/streamlit.exe run src/ui/app.py

Only USAJOBS.gov can be searched directly from the "Run now" button, since
it's a plain HTTP API. ZipRecruiter/Dice/Indeed are MCP connector tools, not
reachable from a standalone script - they're searched daily by the
panga-daily-job-search scheduled task instead (see
docs/daily-job-search-task.md), not this button.

Navigation is a custom top tab bar (Call to Action / Results / Interview Prep
/ Settings) bound to st.session_state, not Streamlit's native st.tabs -
native tabs have no API to switch programmatically, and cross-navigation
(e.g. "Prep for this interview" jumping from Call to Action straight to
Interview Prep with context) needs that. Call to Action is the default/home
tab since that's where things needing a timely reaction actually surface
(design decision 2026-07-30). A persistent alert strip above the tabs
carries the two cross-cutting things that used to live on a single page
(inbox application-match confirmations, drafts waiting to be sent) so they're
visible no matter which tab is open.
"""

import sys
from datetime import datetime
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
from tailoring.interview_prep import load_interview_prep
from prospector.kpis import coverage_summary, activity_summary, outcome_summary
from prospector.rejection_diagnosis import gather_diagnosis_input
from prospector.target_accounts import load_target_accounts, set_status as set_target_account_status
from prospector.outreach import (
    add_outreach, update_status as update_outreach_status, request_draft,
    get_outreach_for_target_account, get_outreach_for_job,
)
from linkedin.storage import load_linkedin_profile, save_snapshot, mark_suggestion_status, get_active_suggestions, SECTIONS as LINKEDIN_SECTIONS
from linkedin.ingest import extract_text_from_pdf
from linkedin.connections import parse_connections_csv, looks_like_recruiter, cross_reference_target_accounts
from linkedin.connections_store import load_connections_snapshot, save_connections
from security.crypto_store import has_recovery_code, generate_recovery_code

CATEGORY_LABELS = {
    "rejection": "Rejection",
    "interview_request": "Interview request",
    "assessment_request": "Assessment / take-home task",
    "offer": "Offer",
    "recruiter_question": "Recruiter question",
}
# Order sections appear in on the Call to Action tab - lead with the ones
# that gain from a fast reply (offers, interviews), rejections last since
# they only need a short acknowledgment, not urgency.
CATEGORY_ORDER = ["offer", "interview_request", "assessment_request", "recruiter_question", "rejection"]

LINKEDIN_SECTION_LABELS = {
    "headline": "Headline",
    "about": "About / Summary",
    "experience": "Experience",
    "skills": "Skills",
    "certifications": "Certifications",
}

SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

st.set_page_config(page_title="Panga - Job Search", layout="wide")


def load_settings() -> dict:
    return yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")


def application_status(job: dict) -> str | None:
    app = get_application(job.get("source"), job.get("job_id"))
    return app["status"] if app else None


def job_label(job: dict) -> str:
    return f"{job.get('title')} - {job.get('organization')}"


OUTREACH_STATUSES = ["planned", "drafted", "sent", "responded", "no_response"]


def render_outreach_section(key_prefix: str, target_account_name: str | None = None, job_source: str | None = None, job_id: str | None = None) -> None:
    """Shared outreach UI (PRD §16b) - called from both the target-account
    detail panel and a job's detail panel, since outreach anchors to
    either one. Logging is manual; email drafting only flags a request -
    the actual Gmail draft is created by the panga-cta-fulfillment
    scheduled task (reused, not a second drafting pathway)."""
    existing = get_outreach_for_target_account(target_account_name) if target_account_name else get_outreach_for_job(job_source, job_id)
    st.markdown("**Outreach**")
    for o in existing:
        label = o["contact_name"] + (f", {o['contact_title']}" if o.get("contact_title") else "")
        st.caption(f"{label} — {o['channel']}" + (f" — {o['notes']}" if o.get("notes") else ""))
        oc1, oc2, oc3 = st.columns([2, 2, 1])
        with oc1:
            new_o_status = st.selectbox(
                "Status", OUTREACH_STATUSES, index=OUTREACH_STATUSES.index(o["status"]),
                key=f"{key_prefix}_ostatus_{o['outreach_id']}", label_visibility="collapsed",
            )
            if new_o_status != o["status"] and st.button("Update", key=f"{key_prefix}_oupdate_{o['outreach_id']}"):
                update_outreach_status(o["outreach_id"], new_o_status)
                st.rerun()
        with oc2:
            if o["channel"] == "email" and o.get("contact_email") and not o.get("gmail_draft_id") and not o.get("draft_requested"):
                if st.button("Request draft", key=f"{key_prefix}_reqdraft_{o['outreach_id']}"):
                    request_draft(o["outreach_id"])
                    st.info("Flagged - the background fulfillment task will create a real Gmail draft shortly.")
                    st.rerun()
            elif o.get("draft_requested"):
                st.caption("Draft requested, not yet created")
        with oc3:
            if o.get("gmail_draft_link"):
                st.link_button("Open draft", o["gmail_draft_link"], key=f"{key_prefix}_opendraft_{o['outreach_id']}")

    with st.expander("Log new outreach"):
        oc_name = st.text_input("Contact name", key=f"{key_prefix}_new_contact_name")
        oc_title = st.text_input("Contact title (optional)", key=f"{key_prefix}_new_contact_title")
        oc_channel = st.selectbox("Channel", ["email", "linkedin", "phone", "in_person"], key=f"{key_prefix}_new_channel")
        oc_email = st.text_input("Contact email (optional, needed to request a drafted email)", key=f"{key_prefix}_new_email") if oc_channel == "email" else None
        oc_notes = st.text_input("Notes (optional)", key=f"{key_prefix}_new_notes")
        if st.button("Log outreach", key=f"{key_prefix}_new_save"):
            if oc_name:
                add_outreach(
                    oc_name, oc_channel, job_source=job_source, job_id=job_id,
                    target_account_name=target_account_name, contact_title=oc_title or None,
                    contact_email=oc_email or None, notes=oc_notes or None,
                )
                st.success("Logged.")
                st.rerun()
            else:
                st.warning("Contact name is required.")


def go_to_prep(target: dict) -> None:
    """Jumps to the Interview Prep tab with enough context to hand off to
    Claude Code - the tab itself doesn't generate anything, it just shows
    what to ask for. target is either {"kind": "job", "source", "job_id",
    "job_label"} (from Results, where the job is known for certain) or
    {"kind": "email", "thread_id", "subject", "sender", "gmail_link"} (from
    Call to Action, where the email might not be linked to a tracked
    application yet - Claude resolves that ambiguity in conversation, same
    as the existing suggest_status matching does)."""
    st.session_state["active_tab"] = "prep"
    st.session_state["prep_target"] = target
    st.rerun()


st.title("Panga - Job Search")

jobs = load_jobs()
all_cta = get_active_cta_emails()
prep_records = load_interview_prep()

cta_count = len(all_cta)
results_count = len(jobs)
prep_in_progress_count = sum(1 for r in prep_records for round_ in r["rounds"] if round_["status"] == "in_progress")

st.session_state.setdefault("active_tab", "cta")

# --- Persistent alert strip: shown above the tabs on every tab, since these
# are time-sensitive and easy to miss if buried under whichever tab happens
# to be open (design decision 2026-07-30, see module docstring). ---
pending_suggestions = get_pending_status_suggestions()
outstanding_drafts = get_awaiting_draft_send()

if pending_suggestions or outstanding_drafts:
    with st.container(border=True):
        for sugg in pending_suggestions:
            job = next((j for j in jobs if j["source"] == sugg["source"] and j["job_id"] == sugg["job_id"]), None)
            label = job_label(job) if job else f"{sugg['source']} {sugg['job_id']}"
            st.markdown(f"Inbox match: mark **{label}** as \"{sugg['suggested_status']}\"?")
            st.caption(sugg.get("suggested_status_reason") or "")
            s1, s2, _ = st.columns([1, 1, 6])
            with s1:
                if st.button("Confirm", key=f"confirm_{sugg['source']}_{sugg['job_id']}"):
                    confirm_status_suggestion(sugg["source"], sugg["job_id"], accept=True)
                    st.rerun()
            with s2:
                if st.button("Dismiss", key=f"dismiss_{sugg['source']}_{sugg['job_id']}"):
                    confirm_status_suggestion(sugg["source"], sugg["job_id"], accept=False)
                    st.rerun()
        if outstanding_drafts:
            st.caption(f"{len(outstanding_drafts)} draft(s) created and waiting in Gmail for you to review and send - this clears itself once you send them.")

# --- Tab bar ---
TABS = [
    ("cta", f"Call to action ({cta_count})" if cta_count else "Call to action"),
    ("results", f"Results ({results_count})"),
    ("prospector", "Prospector"),
    ("prep", f"Interview prep ({prep_in_progress_count})" if prep_in_progress_count else "Interview prep"),
    ("linkedin", "LinkedIn"),
    ("settings", "Settings"),
]
tab_cols = st.columns(len(TABS))
for col, (key, label) in zip(tab_cols, TABS):
    with col:
        if st.button(label, key=f"tab_{key}", type="primary" if st.session_state["active_tab"] == key else "secondary", use_container_width=True):
            st.session_state["active_tab"] = key
            st.rerun()
st.divider()

active_tab = st.session_state["active_tab"]

if active_tab == "settings":
    st.header("Target roles and industries")
    st.caption("These control sort order on the Results tab - higher weight surfaces first. Not a search filter; every source is still searched the same way.")

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

    st.divider()
    st.header("Data Recovery")
    st.caption(
        "Your resume, job history, and applications are encrypted on this "
        "computer using a key stored in this Windows account - not a "
        "password you type in. If this Windows account/profile is ever "
        "lost, or this data is moved to a new computer, that key goes with "
        "it and there's normally no way back in. A recovery code fixes "
        "that: generate one below, and it'll let you regain access without "
        "the original key."
    )

    if has_recovery_code():
        st.caption("A recovery code already exists for this data. Generating a new one below replaces it - the old code stops working.")
    else:
        st.caption("No recovery code has been generated yet - your data has no recovery path if this Windows account is lost.")

    if st.button("Generate recovery code"):
        st.session_state["new_recovery_code"] = generate_recovery_code()

    if st.session_state.get("new_recovery_code"):
        st.warning(
            "Write this down now and save it somewhere OTHER than this "
            "computer - a password manager on your phone, or printed and "
            "filed away. It will not be shown again after you leave this "
            "page."
        )
        st.code(st.session_state["new_recovery_code"], language=None)
        if st.button("I've saved this somewhere safe"):
            del st.session_state["new_recovery_code"]
            st.rerun()

elif active_tab == "cta":
    st.header("Call to Action")
    st.caption("Emails the Gmail scan flagged as needing a reply or a decision.")

    r1, r2 = st.columns([1, 5])
    with r1:
        if st.button("Refresh"):
            st.rerun()

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

            actions = st.columns(4 if cat == "interview_request" else 3)
            with actions[0]:
                st.link_button("Open in Gmail", email["gmail_link"], key=f"open_cta_{email['thread_id']}")
            with actions[1]:
                if email.get("draft_created"):
                    st.link_button("Open draft", email["draft_link"], key=f"draft_link_{email['thread_id']}")
                elif email.get("draft_requested"):
                    st.button("Draft requested...", key=f"draft_pending_{email['thread_id']}", disabled=True)
                else:
                    if st.button("Draft reply", key=f"draft_cta_{email['thread_id']}"):
                        request_draft(email["thread_id"])
                        st.success("Draft requested - will be created in Gmail on the next scan run.")
                        st.rerun()
            with actions[2]:
                if st.button("Dismiss", key=f"dismiss_cta_{email['thread_id']}"):
                    request_archive(email["thread_id"])
                    st.rerun()
            if cat == "interview_request":
                with actions[3]:
                    if st.button("Prep for this interview", key=f"prep_cta_{email['thread_id']}"):
                        go_to_prep({
                            "kind": "email",
                            "thread_id": email["thread_id"],
                            "subject": email.get("subject"),
                            "sender": email.get("sender"),
                            "gmail_link": email["gmail_link"],
                        })
            st.divider()

elif active_tab == "results":
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
        st.caption("This button only covers USAJOBS.gov directly. ZipRecruiter, Dice, and Indeed are searched automatically once a day by the scheduled task instead (they're MCP connector tools, not reachable from this button).")

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

                app_record = get_application(job.get("source"), job.get("job_id")) or {}
                requested = app_record.get("documents_requested") or []
                status = app_record.get("status")

                st.markdown("**Documents for this application**")
                doc_types = [
                    ("resume", "Resume"),
                    ("cover_letter", "Cover letter"),
                    ("exec_bio", "Executive bio"),
                    ("leadership_summary", "Leadership summary"),
                ]
                doc_cols = st.columns(4)
                checked = {}
                for col, (doc_key, doc_label) in zip(doc_cols, doc_types):
                    with col:
                        checked[doc_key] = st.checkbox(
                            doc_label,
                            value=doc_key in requested,
                            key=f"doc_{doc_key}_{job.get('source')}_{job.get('job_id')}",
                        )
                if st.button("Request documents", key=f"reqdocs_{job.get('source')}_{job.get('job_id')}"):
                    selected = [k for k, v in checked.items() if v]
                    upsert_application(job["source"], job["job_id"], status="under review", documents_requested=selected)
                    st.info("Saved. Go to Claude Code and ask to draft the documents for this job - it'll generate exactly what's checked (per the PRD's LLM architecture, not inside this app). Once you've actually submitted it, come back and mark it \"applied.\"")
                    st.rerun()

                doc_field_map = {
                    "resume": "resume_text",
                    "cover_letter": "cover_letter_text",
                    "exec_bio": "exec_bio_text",
                    "leadership_summary": "leadership_summary_text",
                }
                for doc_key, doc_label in doc_types:
                    drafted_text = app_record.get(doc_field_map[doc_key])
                    if drafted_text:
                        with st.expander(f"{doc_label} (drafted)"):
                            st.code(drafted_text, language=None)

                b2, b3 = st.columns(2)
                with b2:
                    if status in ("applied", "interview scheduled"):
                        if st.button("Prep for interview", key=f"prep_results_{job.get('source')}_{job.get('job_id')}"):
                            go_to_prep({
                                "kind": "job",
                                "source": job["source"],
                                "job_id": job["job_id"],
                                "job_label": job_label(job),
                            })
                with b3:
                    new_status = st.selectbox(
                        "Mark status",
                        ["-", "applied", "interview scheduled", "offer", "rejected", "not interested", "save for later"],
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
                st.divider()
                render_outreach_section(f"job_{job.get('source')}_{job.get('job_id')}", job_source=job.get("source"), job_id=job.get("job_id"))

elif active_tab == "prospector":
    st.header("Prospector")
    st.caption(
        "Companies worth watching before they've posted a role, outreach logging, plus "
        "coverage/activity/outcome numbers from your job search so far. The Learn Engine (PRD §17) "
        "isn't built yet."
    )

    settings = load_settings()
    target_roles = settings.get("target_roles", [])
    applications = load_applications()
    target_accounts = load_target_accounts()

    st.subheader("Target accounts")
    st.caption(
        "Sourced from ClinicalTrials.gov Phase 3 activity, recent openFDA drug approvals, "
        "commercial-hiring job postings already in Results, and SEC S-1/IPO filings mentioning "
        "Phase 3 activity (PRD §16a, all 4 signals). Filtered to exclude obvious non-companies "
        "(universities, hospitals, government), known mega-pharma majors, and known-acquired "
        "companies - but not every remaining entry is a great fit (a research consortium, an "
        "unusual NDA holder, or an unrelated industry whose job title happened to match can still "
        "slip through), so treat \"watching\" as a starting point to review, not a verified lead. "
        "Mark anything wrong as \"disqualified\" below."
    )
    if not target_accounts:
        st.info("No target accounts yet.")
    else:
        ta_rows = [{
            "Company": a["company_name"],
            "Status": a["status"],
            "Signals": len(a["signals"]),
            "Industry": a.get("industry") or "-",
        } for a in target_accounts]
        ta_df = pd.DataFrame(ta_rows)
        ta_event = st.dataframe(
            ta_df, hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="single-row", key="target_accounts_table",
        )
        selected_ta_rows = ta_event.selection.rows if ta_event and ta_event.selection else []
        if selected_ta_rows:
            acc = target_accounts[selected_ta_rows[0]]
            st.markdown(f"**{acc['company_name']}**")
            for sig in acc["signals"]:
                st.caption(f"[{sig['signal_type']}, {sig['source']}] {sig['detail']} (observed {sig['date_observed'][:10]})")
            if acc.get("notes"):
                st.caption(f"Notes: {acc['notes']}")
            new_ta_status = st.selectbox(
                "Status", ["watching", "qualified", "contacted", "stale", "disqualified"],
                index=["watching", "qualified", "contacted", "stale", "disqualified"].index(acc["status"]),
                key=f"ta_status_{acc['company_name']}",
            )
            ta_notes = st.text_input("Notes (optional)", value=acc.get("notes") or "", key=f"ta_notes_{acc['company_name']}")
            if st.button("Save", key=f"ta_save_{acc['company_name']}"):
                set_target_account_status(acc["company_name"], new_ta_status, notes=ta_notes or None)
                st.success("Saved.")
                st.rerun()
            st.divider()
            render_outreach_section(f"ta_{acc['company_name']}", target_account_name=acc["company_name"])

    coverage = coverage_summary(jobs)
    activity = activity_summary(applications)
    outcome = outcome_summary(applications, jobs, target_roles)

    st.subheader("Coverage")
    c1, c2, c3 = st.columns(3)
    c1.metric("Jobs found (total)", coverage["total_jobs"])
    c2.metric("Added in last 7 days", coverage["added_last_7_days"])
    c3.metric("Channels", len(coverage["by_channel"]))
    st.dataframe(
        pd.DataFrame(sorted(coverage["by_channel"].items()), columns=["Channel", "Jobs"]),
        hide_index=True, use_container_width=True,
    )
    if coverage["untimestamped"]:
        st.caption(f"{coverage['untimestamped']} job(s) predate 2026-07-30 and have no discovery date, so they're in the total but not the 7-day count.")

    st.subheader("Activity")
    a1, a2 = st.columns(2)
    a1.metric("Applications tracked (total)", activity["total_applications"])
    a2.metric("Started in last 7 days", activity["created_last_7_days"])
    st.dataframe(
        pd.DataFrame(sorted(activity["by_status"].items()), columns=["Status", "Count"]),
        hide_index=True, use_container_width=True,
    )
    if activity["untimestamped"]:
        st.caption(f"{activity['untimestamped']} application(s) predate 2026-07-30 and have no start date, so they're in the total but not the 7-day count.")

    st.subheader("Outcome")
    st.caption(
        "Rates are out of applications that reached \"applied\" or later, using each "
        "application's CURRENT status - an application that was briefly \"interview "
        "scheduled\" before becoming \"offer\" only counts under offer, not both."
    )
    overall = outcome["overall"]
    if overall["applied"] == 0:
        st.info("No applications marked \"applied\" yet - rates will show up here once at least one does.")
    else:
        o1, o2, o3, o4 = st.columns(4)
        o1.metric("Response rate", f"{overall['response_rate']:.0%}")
        o2.metric("Interview rate", f"{overall['interview_rate']:.0%}")
        o3.metric("Offer rate", f"{overall['offer_rate']:.0%}")
        o4.metric("Rejection rate", f"{overall['rejection_rate']:.0%}")
        st.caption(f"Based on {overall['applied']} application(s) that reached \"applied\" or later.")

        def rates_table(by_dimension: dict, dim_label: str) -> pd.DataFrame:
            rows = []
            for key, r in sorted(by_dimension.items(), key=lambda kv: str(kv[0])):
                rows.append({
                    dim_label: key,
                    "Applied": r["applied"],
                    "Response %": r["response_rate"],
                    "Interview %": r["interview_rate"],
                    "Offer %": r["offer_rate"],
                    "Rejection %": r["rejection_rate"],
                })
            return pd.DataFrame(rows)

        with st.expander("By channel"):
            st.dataframe(rates_table(outcome["by_channel"], "Channel"), hide_index=True, use_container_width=True)
        with st.expander("By fit-score band"):
            st.dataframe(rates_table(outcome["by_score_band"], "Score band"), hide_index=True, use_container_width=True)
        with st.expander("By target-role priority weight"):
            st.caption("Weight comes from Settings > target roles - higher weight roles are the ones you prioritized.")
            st.dataframe(rates_table(outcome["by_role_weight"], "Priority weight"), hide_index=True, use_container_width=True)

    st.subheader("Rejection-pattern diagnosis")
    st.caption(
        "Looks for clustering in why applications aren't landing - by score band, channel, "
        "role type, or the reasons you've given for marking something not interested. This "
        "needs Claude's live reasoning, not a canned report, so this button only gathers the "
        "data - come back to Claude Code and ask it to run the diagnosis."
    )
    if st.button("Prepare diagnosis data"):
        diagnosis_input = gather_diagnosis_input(applications, jobs)
        st.session_state["diagnosis_input"] = diagnosis_input

    diagnosis_input = st.session_state.get("diagnosis_input")
    if diagnosis_input:
        d1, d2 = st.columns(2)
        d1.metric("Rejected applications", diagnosis_input["rejected_count"])
        d2.metric("'Not interested' with a reason given", diagnosis_input["not_interested_with_reason_count"])
        if diagnosis_input["rejected_count"] == 0 and diagnosis_input["not_interested_with_reason_count"] == 0:
            st.info("Nothing to diagnose yet - no rejections tracked and no not-interested reasons given so far.")
        else:
            st.success("Data's ready. Go to Claude Code and ask it to run the rejection-pattern diagnosis - it'll read this and give you a plain-language write-up with suggestions.")
            with st.expander("What it'll be looking at"):
                if diagnosis_input["rejected"]:
                    st.markdown("**Rejected**")
                    st.dataframe(pd.DataFrame(diagnosis_input["rejected"]), hide_index=True, use_container_width=True)
                if diagnosis_input["not_interested_with_reason"]:
                    st.markdown("**Not interested (with reason)**")
                    st.dataframe(pd.DataFrame(diagnosis_input["not_interested_with_reason"]), hide_index=True, use_container_width=True)

elif active_tab == "prep":
    st.header("Interview Prep")

    prep_target = st.session_state.get("prep_target")
    if prep_target:
        with st.container(border=True):
            if prep_target["kind"] == "job":
                st.markdown(f"**Ready to prep: {prep_target['job_label']}**")
                st.caption("Go to Claude Code and ask to prep for this interview - it'll research the interviewer(s)/company and draft persona-aware questions and talking points from your master profile.")
            else:
                st.markdown(f"**Ready to prep: \"{prep_target['subject']}\"** from {prep_target['sender']}")
                st.caption("Go to Claude Code and ask to prep for this interview. It'll read the full email thread first to find interviewer/panel names, match it to the right application, then research from there.")
            if st.button("Clear", key="clear_prep_target"):
                st.session_state["prep_target"] = None
                st.rerun()

    if not prep_records:
        st.caption("No interview prep started yet. Use \"Prep for this interview\" on Results or Call to Action once you're past the applied stage.")

    for record in prep_records:
        job = next((j for j in jobs if j["source"] == record["source"] and j["job_id"] == record["job_id"]), None)
        label = job_label(job) if job else f"{record['source']} {record['job_id']}"
        st.subheader(label)

        for round_ in record["rounds"]:
            status_note = "in progress" if round_["status"] == "in_progress" else round_["status"]
            with st.expander(f"{round_['round_label']} - {status_note}", expanded=(round_["status"] == "in_progress")):
                logistics = " - ".join(v for v in [round_.get("date"), round_.get("format")] if v)
                if logistics:
                    st.caption(logistics)

                if round_.get("interviewers"):
                    for person in round_["interviewers"]:
                        st.markdown(f"**{person.get('name')}**" + (f", {person.get('title')}" if person.get("title") else ""))
                        if person.get("research_summary"):
                            st.caption(person["research_summary"])
                        if person.get("persona"):
                            st.markdown(f"_Likely focus:_ {person['persona']}")
                        for link in person.get("research_links") or []:
                            st.caption(link)

                if round_.get("company_snapshot"):
                    st.markdown(f"**Company snapshot:** {round_['company_snapshot']}")

                if round_.get("likely_questions"):
                    st.markdown("**Likely questions**")
                    for q in round_["likely_questions"]:
                        asked_by = f" ({q['asked_by']})" if q.get("asked_by") else ""
                        st.markdown(f"- {q.get('question')}{asked_by}")
                        if q.get("why"):
                            st.caption(q["why"])
                        if q.get("talking_point"):
                            st.markdown(f"  > {q['talking_point']}")

                if round_.get("questions_to_ask"):
                    st.markdown("**Questions to ask them**")
                    for q in round_["questions_to_ask"]:
                        best_for = f" (best for {q['best_for']})" if q.get("best_for") else ""
                        st.markdown(f"- {q.get('question')}{best_for}")

elif active_tab == "linkedin":
    st.header("LinkedIn Profile Enhancement")
    st.caption(
        "Upload a PDF export of your current LinkedIn profile below - either "
        "LinkedIn's own \"Save to PDF\" (from your profile page: click \"More\" "
        "under your name, then \"Save to PDF\"), or a browser print-to-PDF of "
        "the profile page. Both are things you export yourself from your own "
        "logged-in session - Panga never logs into LinkedIn, scrapes it, or "
        "posts anything there itself. Then go to Claude Code and ask to "
        "analyze/enhance your LinkedIn profile - that's where the comparison "
        "against your master profile and target-role skills happens, and "
        "where suggested rewrites get drafted. Suggestions below are yours to "
        "copy and paste into LinkedIn's own edit screens."
    )

    linkedin_data = load_linkedin_profile()

    uploaded_files = st.file_uploader(
        "LinkedIn profile PDF(s)", type=["pdf"], accept_multiple_files=True,
        help="You can upload one file or several (e.g. the LinkedIn export and a printed profile page) - text from all of them is combined.",
    )

    if st.button("Save profile", type="primary", disabled=not uploaded_files):
        parts = []
        source_files = []
        for f in uploaded_files:
            text = extract_text_from_pdf(f)
            parts.append(f"--- {f.name} ---\n{text}")
            source_files.append(f.name)
        save_snapshot("\n\n".join(parts), source_files, saved_at=datetime.now().isoformat(timespec="seconds"))
        st.success("Saved. Go to Claude Code and ask to analyze/enhance your LinkedIn profile.")
        st.rerun()

    if linkedin_data.get("last_saved"):
        st.caption(f"Last saved: {linkedin_data['last_saved']} (from {', '.join(linkedin_data.get('source_files', []))})")

    st.divider()

    if linkedin_data.get("last_analyzed"):
        score = linkedin_data.get("profile_strength_score")
        if score is not None:
            st.metric("Profile strength", f"{score}/100")
            if linkedin_data.get("profile_strength_rationale"):
                st.caption(linkedin_data["profile_strength_rationale"])
        st.caption(f"Last analyzed: {linkedin_data['last_analyzed']}")

        active_suggestions = get_active_suggestions()
        if not active_suggestions:
            st.caption("No open suggestions - everything's either applied or dismissed.")
        for section in LINKEDIN_SECTIONS:
            section_suggestions = [s for s in active_suggestions if s["section"] == section]
            if not section_suggestions:
                continue
            st.subheader(LINKEDIN_SECTION_LABELS[section])
            for s in section_suggestions:
                if s.get("rationale"):
                    st.caption(s["rationale"])
                st.markdown("Suggested text (copy this into LinkedIn):")
                st.code(s["suggested_text"], language=None)
                b1, b2 = st.columns([1, 1])
                with b1:
                    if st.button("Mark updated on LinkedIn", key=f"applied_{s['id']}"):
                        mark_suggestion_status(s["id"], "applied")
                        st.rerun()
                with b2:
                    if st.button("Dismiss", key=f"dismissed_{s['id']}"):
                        mark_suggestion_status(s["id"], "dismissed")
                        st.rerun()
                st.divider()
    else:
        st.caption("Not yet analyzed - upload your profile PDF(s) above, save, then ask Claude to analyze it.")

    st.divider()
    st.subheader("Connections (for Prospector outreach)")
    st.caption(
        "Upload your LinkedIn connections export (Settings > Data Privacy > "
        "\"Get a copy of your data\" > check Connections only, then download "
        "the resulting CSV) to help find who to reach out to on the "
        "Prospector tab (§16b): connections whose title looks like a "
        "recruiter, and connections who work at a company already in your "
        "target accounts list. Same manual-export-only rule as everything "
        "else here - nothing is ever scraped or pulled automatically."
    )
    connections_snapshot = load_connections_snapshot()
    uploaded_csv = st.file_uploader("LinkedIn connections CSV", type=["csv"], key="connections_csv")
    if st.button("Save connections", type="primary", disabled=not uploaded_csv):
        parsed = parse_connections_csv(uploaded_csv)
        save_connections(parsed, uploaded_csv.name, saved_at=datetime.now().isoformat(timespec="seconds"))
        st.success(f"Saved {len(parsed)} connection(s).")
        st.rerun()

    if connections_snapshot.get("last_saved"):
        conns = connections_snapshot["connections"]
        st.caption(f"Last saved: {connections_snapshot['last_saved']} ({len(conns)} connections from {connections_snapshot.get('source_file')})")

        recruiters = [c for c in conns if looks_like_recruiter(c.get("position"))]
        target_names = [a["company_name"] for a in load_target_accounts()]
        target_matches = cross_reference_target_accounts(conns, target_names)

        rc1, rc2 = st.columns(2)
        rc1.metric("Recruiter connections", len(recruiters))
        rc2.metric("Connections at a target account", len(target_matches))

        if recruiters:
            with st.expander("Recruiter connections"):
                st.dataframe(pd.DataFrame(recruiters)[["first_name", "last_name", "company", "position"]], hide_index=True, use_container_width=True)
        if target_matches:
            with st.expander("Connections at a target account"):
                st.dataframe(pd.DataFrame(target_matches)[["first_name", "last_name", "company", "position", "matched_target_account"]], hide_index=True, use_container_width=True)
