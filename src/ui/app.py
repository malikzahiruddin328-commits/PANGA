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

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _find_bhangi_src(project_root: Path) -> Path | None:
    """Locate the sibling Bhangi checkout's src/ directory.

    Bhangi is a separate, standalone project (shared issue store for the
    Support tab, reused across projects), normally a sibling folder to the
    main Panga checkout - see ../Bhangi/README.md. But every git worktree
    branch lives under Panga/.claude/worktrees/<branch>/, where
    project_root.parent has no Bhangi folder. Walk up the ancestor chain
    (which passes through the real Panga checkout for any worktree) looking
    for an ancestor whose sibling is Bhangi. BHANGI_PATH env var overrides
    this search entirely.
    """
    override = os.environ.get("BHANGI_PATH")
    if override:
        candidate = Path(override) / "src"
        if candidate.is_dir():
            return candidate
    for ancestor in (project_root, *project_root.parents):
        candidate = ancestor.parent / "Bhangi" / "src"
        if candidate.is_dir():
            return candidate
    return None


_bhangi_src = _find_bhangi_src(PROJECT_ROOT)
if _bhangi_src is not None:
    sys.path.insert(0, str(_bhangi_src))

import pandas as pd
import streamlit as st
import yaml

from search.usajobs import search_jobs, USAJobsNotConfigured
from search.job_store import load_jobs
from ranking.prioritize import weight_for, dedupe_across_sources
from tailoring.applications import load_applications, upsert_application, get_application, get_pending_status_suggestions, confirm_status_suggestion, set_strategy_tag, needs_edit_review, record_document_edit_review
from tailoring.cta_emails import get_active_cta_emails, request_archive, request_draft, get_awaiting_draft_send
from tailoring.interview_prep import load_interview_prep, record_round_outcome
from tailoring.dossier import dossier_dir, sync_workspace_documents, check_for_edits
from tailoring.drafting import generate_documents, score_job, save_gap_answers, is_configured as drafting_is_configured, DraftingNotConfigured, DraftingFailed
from prospector.kpis import coverage_summary, activity_summary, outcome_summary
from prospector.rejection_diagnosis import gather_diagnosis_input
from prospector.target_accounts import load_target_accounts, set_status as set_target_account_status, set_website, load_website_lookup_cost, save_website_lookup_cost
from prospector.company_lookup import lookup_company_website
from prospector.outreach import (
    add_outreach, update_status as update_outreach_status, request_draft,
    get_outreach_for_target_account, get_outreach_for_job, load_outreach,
    set_strategy_tag as set_outreach_strategy_tag,
)
from prospector.learn_engine import gather_learn_engine_input
from prospector.prospector_score import load_prospector_score, gather_prospector_score_input, compute_prospector_score, save_prospector_score
from linkedin.storage import load_linkedin_profile, save_snapshot, mark_suggestion_status, get_active_suggestions, SECTIONS as LINKEDIN_SECTIONS
from linkedin.ingest import extract_text_from_pdf
from linkedin.connections import parse_connections_csv, looks_like_recruiter, cross_reference_target_accounts
from linkedin.connections_store import load_connections_snapshot, save_connections
from security.crypto_store import has_recovery_code, generate_recovery_code
from feedback.ui_feedback import get_open_feedback, mark_resolved
from ui.feedback_widget import render_feedback_widget
from profile.ingest import load_manifest_result, remove_document, ingest_uploaded_document
from profile.storage import load_profile
from bhangi.ui import render_support_page
from ui.license_gate import render_indicator_and_get_block, render_block_screen
from licensing.client import release_device, create_portal_session, LicenseNetworkError, LicenseServiceError

BHANGI_PROJECT = "panga"

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

# Color-coding for the Call to Action tab (feedback from Zahir 2026-07-30:
# the higher-stakes/time-sensitive categories should stand out from routine
# ones). Uses only native st.badge semantic colors, no custom CSS.
CATEGORY_COLORS = {
    "offer": "green",
    "interview_request": "violet",
    "assessment_request": "orange",
    "recruiter_question": "gray",
    "rejection": "gray",
}
CATEGORY_URGENCY = {
    "offer": "Act now",
    "interview_request": "Time-sensitive",
    "assessment_request": "Time-sensitive",
    "recruiter_question": "Routine",
    "rejection": "No action needed",
}

LINKEDIN_SECTION_LABELS = {
    "headline": "Headline",
    "about": "About / Summary",
    "experience": "Experience",
    "skills": "Skills",
    "certifications": "Certifications",
}

SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
THEMES_DIR = PROJECT_ROOT / ".streamlit" / "themes"
CONFIG_PATH = PROJECT_ROOT / ".streamlit" / "config.toml"

st.set_page_config(page_title="Panga - Job Search", page_icon=":material/work:", layout="wide")


def load_settings() -> dict:
    return yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")


def application_status(job: dict) -> str | None:
    app = get_application(job.get("source"), job.get("job_id"))
    return app["status"] if app else None


def job_label(job: dict) -> str:
    return f"{job.get('title')} - {job.get('organization')}"


def format_pay(value) -> str | None:
    """pay_min/pay_max come from several sources (USAJOBS numeric strings,
    Indeed's parsed compensation text, SmartRecruiters custom fields) - not
    guaranteed to be clean digits, so a value that doesn't parse as a number
    is shown as-is rather than crashing."""
    if value in (None, ""):
        return None
    try:
        num = float(str(value).replace(",", "").replace("$", ""))
    except ValueError:
        return str(value)
    return f"{num:,.0f}" if num == int(num) else f"{num:,.2f}"


def format_timestamp(value: str | None) -> str:
    """Renders a stored ISO timestamp (e.g. "2026-07-30T16:15:55.123456+00:00")
    as something a non-technical reader can parse at a glance (e.g. "Jul 30,
    2026 at 4:15 PM") - Zahir's explicit ask 2026-07-31: the raw ISO string
    ("...30T16:15:55...") reads as noise to anyone who doesn't already know
    it's a timestamp. Falls back to the raw string on anything that doesn't
    parse (a malformed/legacy value shouldn't crash the page over cosmetics)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt.strftime("%b %-d, %Y at %-I:%M %p") if sys.platform != "win32" else dt.strftime("%b %#d, %Y at %#I:%M %p")


def left_aligned_columns(df: pd.DataFrame, extra: dict | None = None) -> dict:
    """Column config that left-aligns every column of a dataframe (Streamlit
    right-aligns numeric columns by default) and leaves width unset so each
    column auto-sizes to its own content - Zahir's explicit request
    2026-07-31 applied app-wide, not just one table. `extra` lets a call
    site override specific columns (e.g. a LinkColumn for a URL column) -
    those win over the plain left-aligned default."""
    config = {}
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            config[col] = st.column_config.NumberColumn(alignment="left")
        else:
            config[col] = st.column_config.TextColumn(alignment="left")
    if extra:
        config.update(extra)
    return config


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
        st.markdown(f"{label} — {o['channel']}" + (f" — {o['notes']}" if o.get("notes") else ""))
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
                    st.toast("Flagged - the background fulfillment task will create a real Gmail draft shortly.", icon=":material/check_circle:")
                    st.rerun()
            elif o.get("draft_requested"):
                st.markdown("Draft requested, not yet created")
        with oc3:
            if o.get("gmail_draft_link"):
                st.link_button("Open draft", o["gmail_draft_link"], key=f"{key_prefix}_opendraft_{o['outreach_id']}")
        new_o_tag = st.text_input(
            "Strategy tag (optional)", value=o.get("strategy_tag") or "",
            key=f"{key_prefix}_otag_{o['outreach_id']}", label_visibility="collapsed",
            placeholder="Strategy tag (optional)",
        )
        if new_o_tag != (o.get("strategy_tag") or "") and st.button("Save tag", key=f"{key_prefix}_osavetag_{o['outreach_id']}"):
            set_outreach_strategy_tag(o["outreach_id"], new_o_tag)
            st.rerun()

    with st.expander("Log new outreach"):
        oc_name = st.text_input("Contact name", key=f"{key_prefix}_new_contact_name")
        oc_title = st.text_input("Contact title (optional)", key=f"{key_prefix}_new_contact_title")
        oc_channel = st.selectbox("Channel", ["email", "linkedin", "phone", "in_person"], key=f"{key_prefix}_new_channel")
        oc_email = st.text_input("Contact email (optional, needed to request a drafted email)", key=f"{key_prefix}_new_email") if oc_channel == "email" else None
        oc_notes = st.text_area("Notes (optional)", key=f"{key_prefix}_new_notes")
        if st.button("Log outreach", key=f"{key_prefix}_new_save"):
            if oc_name:
                add_outreach(
                    oc_name, oc_channel, job_source=job_source, job_id=job_id,
                    target_account_name=target_account_name, contact_title=oc_title or None,
                    contact_email=oc_email or None, notes=oc_notes or None,
                )
                st.toast("Logged.", icon=":material/check_circle:")
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


title_col, license_col = st.columns([5, 1])
with title_col:
    st.title("Panga - Job Search")
with license_col:
    license_block = render_indicator_and_get_block()

if license_block is not None:
    render_block_screen(license_block)
    st.stop()

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
            st.markdown(sugg.get("suggested_status_reason") or "")
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
            st.markdown(f"{len(outstanding_drafts)} draft(s) created and waiting in Gmail for you to review and send - this clears itself once you send them.")

# --- Tab bar ---
SIGNAL_TYPE_LABELS = {
    "late_stage_trial": "Late-stage clinical trial",
    "commercial_hiring": "Commercial-build hiring",
    "funding_event": "Funding/IPO filing",
    "regulatory_filing": "Regulatory filing (deprecated signal - see note above)",
}
TAB_ICONS = {
    "cta": ":material/notifications_active:",
    "results": ":material/work:",
    "prospector": ":material/travel_explore:",
    "prep": ":material/school:",
    "linkedin": ":material/badge:",
    "support": ":material/support_agent:",
    "settings": ":material/settings:",
}
TABS = [
    ("cta", f"Call to action ({cta_count})" if cta_count else "Call to action"),
    ("results", f"Results ({results_count})"),
    ("prospector", "Prospector"),
    ("prep", f"Interview prep ({prep_in_progress_count})" if prep_in_progress_count else "Interview prep"),
    ("linkedin", "LinkedIn"),
    ("support", "Support"),
    ("settings", "Settings"),
]
tab_cols = st.columns(len(TABS))
for col, (key, label) in zip(tab_cols, TABS):
    with col:
        if st.button(label, key=f"tab_{key}", icon=TAB_ICONS[key], type="primary" if st.session_state["active_tab"] == key else "secondary", width="stretch"):
            st.session_state["active_tab"] = key
            st.rerun()
st.divider()

active_tab = st.session_state["active_tab"]

if active_tab == "settings":
    render_feedback_widget("settings")

    st.header("Your documents")
    st.markdown(
        "One shared place to manage everything Panga reads from - your "
        "resume and other background documents, your LinkedIn profile "
        "export, and your LinkedIn connections export."
    )

    st.subheader("Resume & other source documents")
    st.markdown(
        "\"Resume\" feeds the gap-probing interview directly. \"Context\" is "
        "background material like an executive bio or leadership summary. "
        "\"Reference\" is an example of a previously-tailored application - "
        "used as a style example, not copied verbatim."
    )
    manifest_entries = load_manifest_result()
    if manifest_entries:
        manifest_df = pd.DataFrame([{
            "File": e["source_file"],
            "Category": e["category"],
            "Target title": e.get("target_title") or "-",
            "Words": e["word_count"],
        } for e in manifest_entries])
        st.dataframe(manifest_df, hide_index=True, width="stretch", column_config=left_aligned_columns(manifest_df))
        remove_choice = st.selectbox(
            "Remove a document", ["-"] + [e["source_file"] for e in manifest_entries],
            key="remove_doc_choice",
        )
        if remove_choice != "-" and st.button("Remove selected document"):
            remove_document(remove_choice)
            st.toast(f"Removed {remove_choice}.", icon=":material/check_circle:")
            st.rerun()
    else:
        st.markdown("No documents uploaded yet.")

    doc_category = st.selectbox(
        "Document type", ["resume", "context", "reference"], key="new_doc_category",
    )
    doc_target_title = None
    doc_note = None
    if doc_category == "resume":
        doc_target_title = st.text_input("Which title does this resume version target? (optional)", key="new_doc_title")
    elif doc_category == "reference":
        doc_note = st.text_area("What was this tailored for? (optional)", key="new_doc_note")
    new_doc_file = st.file_uploader("Upload a .docx or .pdf", type=["docx", "pdf"], key="new_doc_file")
    if st.button("Save document", type="primary", disabled=not new_doc_file):
        entry = ingest_uploaded_document(
            new_doc_file, new_doc_file.name, doc_category,
            target_title=doc_target_title or None, note=doc_note or None,
        )
        st.toast(f"Saved {entry['source_file']} ({entry['word_count']} words).", icon=":material/check_circle:")
        st.rerun()

    st.divider()
    st.subheader("LinkedIn profile")
    st.markdown(
        "Upload a PDF export of your current LinkedIn profile - either "
        "LinkedIn's own \"Save to PDF\" (from your profile page: click "
        "\"More\" under your name, then \"Save to PDF\"), or a browser "
        "print-to-PDF of the profile page. Both are things you export "
        "yourself from your own logged-in session - Panga never logs into "
        "LinkedIn, scrapes it, or posts anything there itself. Analysis and "
        "suggestions show up on the LinkedIn tab."
    )
    linkedin_data = load_linkedin_profile()
    uploaded_linkedin_files = st.file_uploader(
        "LinkedIn profile PDF(s)", type=["pdf"], accept_multiple_files=True, key="settings_linkedin_pdf",
        help="You can upload one file or several (e.g. the LinkedIn export and a printed profile page) - text from all of them is combined.",
    )
    if st.button("Save LinkedIn profile", type="primary", disabled=not uploaded_linkedin_files):
        parts = []
        source_files = []
        for f in uploaded_linkedin_files:
            text = extract_text_from_pdf(f)
            parts.append(f"--- {f.name} ---\n{text}")
            source_files.append(f.name)
        save_snapshot("\n\n".join(parts), source_files, saved_at=datetime.now().isoformat(timespec="seconds"))
        st.toast("Saved. Go to the LinkedIn tab, or ask Claude Code to analyze/enhance your profile.", icon=":material/check_circle:")
        st.rerun()
    if linkedin_data.get("last_saved"):
        st.markdown(f"Last saved: {linkedin_data['last_saved']} (from {', '.join(linkedin_data.get('source_files', []))})")

    st.divider()
    st.subheader("LinkedIn connections")
    st.markdown(
        "Upload your LinkedIn connections export (Settings > Data Privacy > "
        "\"Get a copy of your data\" > check Connections only, then download "
        "the resulting CSV) to help find who to reach out to on the "
        "Prospector tab: connections whose title looks like a recruiter, and "
        "connections who work at a company already in your target accounts "
        "list."
    )
    connections_snapshot = load_connections_snapshot()
    uploaded_connections_csv = st.file_uploader("LinkedIn connections CSV", type=["csv"], key="settings_connections_csv")
    if st.button("Save connections", type="primary", disabled=not uploaded_connections_csv):
        parsed = parse_connections_csv(uploaded_connections_csv)
        save_connections(parsed, uploaded_connections_csv.name, saved_at=datetime.now().isoformat(timespec="seconds"))
        st.toast(f"Saved {len(parsed)} connection(s).", icon=":material/check_circle:")
        st.rerun()
    if connections_snapshot.get("last_saved"):
        st.markdown(f"Last saved: {connections_snapshot['last_saved']} ({len(connections_snapshot['connections'])} connections from {connections_snapshot.get('source_file')})")

    st.divider()
    st.header("Target roles and industries")
    st.markdown("These control sort order on the Results tab - higher weight surfaces first. Not a search filter; every source is still searched the same way.")

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
    st.markdown("See \"Occupations and job series\" on usajobs.gov for codes. This runs alongside keyword search (not instead of it) - government classification is inconsistent, so restricting to one series alone would miss real matches filed under a different code. The compatibility score, not this filter, is what actually screens for relevance.")
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
    st.header("Data recovery")
    st.markdown(
        "Your resume, job history, and applications are encrypted on this "
        "computer using a key stored in this Windows account - not a "
        "password you type in. If this Windows account/profile is ever "
        "lost, or this data is moved to a new computer, that key goes with "
        "it and there's normally no way back in. A recovery code fixes "
        "that: generate one below, and it'll let you regain access without "
        "the original key."
    )

    if has_recovery_code():
        st.markdown("A recovery code already exists for this data. Generating a new one below replaces it - the old code stops working.")
    else:
        st.markdown("No recovery code has been generated yet - your data has no recovery path if this Windows account is lost.")

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

    st.divider()
    st.header("License & billing")
    st.markdown(
        "This license is bound to this computer. Deactivating releases it "
        "immediately so a new device can activate — use this when you're "
        "moving to a new computer, not if this one is lost or stolen (that "
        "needs a manual support review instead)."
    )
    if st.button("Deactivate this device", key="license_deactivate_device"):
        try:
            release_device()
            st.session_state.pop("license_check_result", None)
            st.success("Device deactivated. A new device can now activate this license.")
        except LicenseNetworkError:
            st.error("Couldn't reach the license service — check your internet connection and try again.")
        except LicenseServiceError as e:
            if e.status_code == 429:
                st.error("A device was already transferred within the last 30 days — contact support for an exception.")
            else:
                st.error(f"Couldn't deactivate this device: {e}")

    if st.button("Manage subscription", key="license_manage_billing"):
        try:
            portal = create_portal_session(return_url="http://localhost:8501")
            st.link_button("Open billing portal", portal["portal_url"])
        except LicenseNetworkError:
            st.error("Couldn't reach the billing service — check your internet connection and try again.")
        except LicenseServiceError as e:
            st.error(f"Couldn't open the billing portal: {e}")

    st.divider()
    st.header("UI feedback")
    st.markdown(
        "Voice/text notes left on any screen via \"Leave feedback on this "
        "screen\" - come back to Claude Code and ask it to work through "
        "these. Mark one reviewed once it's been acted on (or you've "
        "decided against it)."
    )
    open_feedback = get_open_feedback()
    if not open_feedback:
        st.markdown("Nothing queued right now.")
    else:
        for fb in open_feedback:
            fc1, fc2 = st.columns([5, 1])
            with fc1:
                st.markdown(f"**{fb['section']}** — {format_timestamp(fb['created_at'])}")
                st.markdown(fb["note"])
            with fc2:
                if st.button("Mark reviewed", key=f"fb_resolve_{fb['id']}"):
                    mark_resolved(fb["id"])
                    st.rerun()
            st.divider()

    st.divider()
    st.header("Appearance")
    st.markdown(
        "Pick a color theme. Applies live once you save - no restart needed, "
        "just pick and the app re-renders in the new colors. The 4 colored "
        "themes each also carry a light and a dark mode - once one is applied, "
        "switch between its light/dark from Streamlit's own ≡ menu "
        "(top right) → Settings."
    )
    theme_options = {
        "Teal": "teal", "Blue": "blue", "Coral": "coral", "Slate purple": "purple",
        "Light (plain, no accent color)": "light", "Dark (plain, no accent color)": "dark",
    }
    theme_choice = st.selectbox("Theme", list(theme_options.keys()), key="theme_choice")
    if st.button("Apply theme"):
        theme_file = THEMES_DIR / f"{theme_options[theme_choice]}.toml"
        CONFIG_PATH.write_text(theme_file.read_text(encoding="utf-8"), encoding="utf-8")
        st.toast(f"{theme_choice} theme applied.", icon=":material/check_circle:")
        st.rerun()

elif active_tab == "cta":
    render_feedback_widget("cta")

    st.header("Call to action")
    st.markdown("Emails the Gmail scan flagged as needing a reply or a decision.")

    r1, r2 = st.columns([1, 5])
    with r1:
        if st.button("Refresh"):
            st.rerun()

    counts = {cat: sum(1 for e in all_cta if e.get("category") == cat) for cat in CATEGORY_ORDER}
    stat_cols = st.columns(len(CATEGORY_ORDER))
    for col, cat in zip(stat_cols, CATEGORY_ORDER):
        with col:
            st.badge(CATEGORY_LABELS[cat], color=CATEGORY_COLORS[cat])
            st.metric("Count", counts[cat], label_visibility="collapsed")

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
        st.markdown("Nothing needs attention right now.")
    elif not filtered:
        st.markdown("No matches for that filter/search.")

    for cat in CATEGORY_ORDER:
        cat_emails = [e for e in filtered if e.get("category") == cat]
        if not cat_emails:
            continue
        st.badge(CATEGORY_URGENCY[cat], color=CATEGORY_COLORS[cat])
        st.subheader(CATEGORY_LABELS[cat])
        for email in cat_emails:
            st.markdown(f"**{email.get('subject')}**")
            st.markdown(f"{email.get('sender')} - {email.get('date')}")
            if email.get("snippet"):
                st.markdown(email["snippet"])

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
                        st.toast("Draft requested - will be created in Gmail on the next scan run.", icon=":material/check_circle:")
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
    render_feedback_widget("results")

    settings = load_settings()

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("Run now (USAJOBS)", type="primary"):
            # Real "i of N" progress instead of an opaque spinner (Zahir's
            # explicit ask 2026-07-31, same request applied to every
            # spinner in the app) - each target role/job-series code is a
            # real, separate search call, so the step count is genuine,
            # not simulated.
            try:
                from search.job_store import save_jobs
                target_roles = settings.get("target_roles", [])
                job_series = settings.get("usajobs_job_series", [])
                steps = [("role", r["name"]) for r in target_roles] + [("series", c) for c in job_series]
                total_new = 0
                if steps:
                    search_bar = st.progress(0, text=f"Searching 1 of {len(steps)}: {steps[0][1]}...")
                    for i, (kind, value) in enumerate(steps, start=1):
                        search_bar.progress((i - 1) / len(steps), text=f"Searching {i} of {len(steps)}: {value}...")
                        if kind == "role":
                            results = search_jobs(keyword=value, results_per_page=50)
                        else:
                            results = search_jobs(job_category_code=value, results_per_page=100)
                        total_new += save_jobs(results)
                    search_bar.progress(1.0, text="Done.")
                st.success(f"Found {total_new} new job(s).")
            except USAJobsNotConfigured as e:
                st.error(str(e))
    with col2:
        st.markdown("This button only covers USAJOBS.gov directly. ZipRecruiter, Dice, and Indeed are searched automatically once a day by the scheduled task instead (they're MCP connector tools, not reachable from this button).")

    with st.expander("Add a job manually (e.g. from LinkedIn)"):
        st.markdown(
            "LinkedIn has no public search API and blocks scraping, so this "
            "is the one channel that works by pasting the posting yourself "
            "instead of an automated search. The description is saved now "
            "rather than fetched live later like other channels, since "
            "LinkedIn URLs often can't be reliably refetched (login wall/"
            "bot-check)."
        )
        manual_job_fields = ["manual_job_title", "manual_job_org", "manual_job_location", "manual_job_url", "manual_job_description"]
        # Same pattern as the feedback widget's clear-after-save: a widget's
        # own session_state key can only be reset BEFORE that widget is
        # instantiated in a given run, so the save handler below only sets
        # a pending flag - the actual clear happens here, at the top of the
        # next run.
        if st.session_state.pop("manual_job_clear_pending", False):
            for field in manual_job_fields:
                st.session_state[field] = ""
        manual_title = st.text_input("Job title", key="manual_job_title")
        manual_org = st.text_input("Organization", key="manual_job_org")
        manual_location = st.text_input("Location", key="manual_job_location")
        manual_url = st.text_input("Posting URL", key="manual_job_url")
        manual_source = st.text_input("Source", value="linkedin", key="manual_job_source")
        manual_description = st.text_area(
            "Paste the job description text", key="manual_job_description", height=200,
        )
        if st.button("Save job", type="primary", disabled=not (manual_title and manual_org and manual_url)):
            from search.job_store import add_manual_job, update_job_score
            job = add_manual_job(
                title=manual_title, organization=manual_org, location=manual_location,
                description=manual_description, posting_url=manual_url, source=manual_source or "linkedin",
            )
            # A job with no fit_score is hidden by the Results tab regardless
            # of the slider (see the min_score filter above) - without this,
            # a manually-added job would sit invisible until the next daily
            # scoring pass or a live Claude Code conversation scored it.
            if drafting_is_configured():
                # Real streaming progress instead of a spinner (Zahir's
                # explicit ask 2026-07-31, "same needs to be here and all
                # other spinner places") - same thinking/writing
                # character-count mechanism as document drafting.
                score_bar = st.progress(0, text="Scoring compatibility...")

                def _update_score_progress(substatus):
                    score_bar.progress(0.5, text=f"Scoring compatibility - {substatus}")

                try:
                    scored = score_job(job, load_profile(), on_progress=_update_score_progress)
                except (DraftingNotConfigured, DraftingFailed) as exc:
                    score_bar.empty()
                    st.toast(f"Saved, but scoring failed: {exc}", icon=":material/warning:")
                else:
                    score_bar.progress(1.0, text="Done.")
                    update_job_score(job["source"], job["job_id"], scored["fit_score"], scored["fit_rationale"])
                    st.toast(
                        f"Saved \"{job['title']}\" at {job['organization']} - scored {scored['fit_score']}/100.",
                        icon=":material/check_circle:",
                    )
            else:
                st.toast(
                    f"Saved \"{job['title']}\" at {job['organization']}. No Anthropic API key configured, so it's "
                    "unscored for now - it'll appear once the daily scoring pass runs, or add an API key to score it immediately.",
                    icon=":material/info:",
                )
            st.session_state["manual_job_clear_pending"] = True
            st.rerun()

    target_roles = settings.get("target_roles", [])

    def sort_key(job):
        has_score = "fit_score" in job
        return (has_score, job.get("fit_score", -1), weight_for(job.get("title"), target_roles))

    ranked = sorted(jobs, key=sort_key, reverse=True)

    unscored_count = sum(1 for j in jobs if "fit_score" not in j)
    scored_count = len(jobs) - unscored_count
    min_score = st.slider(
        "Minimum compatibility score",
        0, 100, 70,
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

    # Cross-source dedup (2026-07-30): if a company has a direct-site channel
    # (Eisai/AbbVie/IQVIA via company_sites.py) and the same real opening also
    # shows up on a job board (Indeed/ZipRecruiter/Dice/USAJOBS/industry
    # boards), the direct-site posting is authoritative - show only that one.
    # See ranking.prioritize.dedupe_across_sources() for the matching rule.
    ranked = dedupe_across_sources(ranked)

    if unscored_count:
        st.markdown(f"{unscored_count} job(s) found but not yet compatibility-scored - hidden until the next scoring pass (daily scheduled task, or ask Claude to score them now).")

    st.subheader(f"{len(ranked)} job(s)")

    # Grouped by channel (source) per Zahir's request 2026-07-29 - each
    # channel (USAJOBS, Dice, ZipRecruiter, etc.) gets its own section.
    # Dedup within a channel happens below (job_store only keys on
    # source+job_id, so re-announced postings aren't caught there). Dedup
    # ACROSS channels also now happens, but only for the direct-company-site
    # case (see dedupe_across_sources() above, called before this point) -
    # a job board posting matching a company's own direct listing is folded
    # into it there. Two different job boards showing the same real job
    # (e.g. Indeed + ZipRecruiter, neither being the company's own site) is
    # still NOT merged - different platforms format titles/orgs too
    # differently for exact matching to be reliable without a company-site
    # anchor to match against, and the risk of wrongly merging two different
    # jobs is higher than the clutter cost.
    channels = list(dict.fromkeys(j["source"] for j in ranked))  # first-appearance order, dedup'd

    def dedupe_key(job):
        # Same title+org+location+pay within a channel = almost certainly the
        # same real job posted under two announcements (e.g. merit-promotion
        # + open-competitive on USAJOBS) - confirmed 2026-07-29 with the two
        # identical "Audit Director (IT)" postings.
        return (job.get("title"), job.get("organization"), job.get("location"), job.get("pay_min"), job.get("pay_max"))

    for channel in channels:
        channel_jobs = [j for j in ranked if j["source"] == channel]

        groups = {}
        for job in channel_jobs:
            groups.setdefault(dedupe_key(job), []).append(job)
        deduped = [postings[0] for postings in groups.values()]  # first (highest-ranked) as primary
        postings_by_primary = {
            id(postings[0]): postings + postings[0].get("_cross_source_duplicates", [])
            for postings in groups.values()
        }

        dup_notes = []
        if len(deduped) < len(channel_jobs):
            dup_notes.append(f"{len(channel_jobs) - len(deduped)} duplicate posting(s) merged")
        cross_source_merged = sum(len(job.get("_cross_source_duplicates", [])) for job in deduped)
        if cross_source_merged:
            dup_notes.append(f"{cross_source_merged} job-board posting(s) matched to this direct listing")
        dup_note = f", {'; '.join(dup_notes)}" if dup_notes else ""
        with st.expander(f"{channel} ({len(deduped)}{dup_note})", expanded=True):
            table_rows = []
            for job in deduped:
                pay_min, pay_max = format_pay(job.get("pay_min")), format_pay(job.get("pay_max"))
                pay = f"${pay_min or '?'}-${pay_max or '?'}" if (pay_min or pay_max) else (job.get("salary_text") or "")
                table_rows.append({
                    "Role": job.get("title"),
                    "Organization": job.get("organization"),
                    "Pay": pay,
                    "Score": job.get("fit_score"),
                    "Status": application_status(job) or "-",
                    "Posting": job.get("posting_url"),
                })
            df = pd.DataFrame(table_rows)

            # Re-tried 2026-07-31 (2nd attempt, same day as the first
            # revert) - Zahir's explicit ask: no checkbox, activate the row
            # by clicking the Role cell itself. The first attempt was
            # reverted because it couldn't be verified end-to-end in this
            # environment; this retry uses Streamlit 1.60's ButtonColumn
            # on_click callback, confirmed real via the installed version's
            # own reference docs and signature (not assumed from memory).
            # Still couldn't click-test the canvas-rendered grid itself in
            # this sandbox (same limitation as the first attempt) - verify
            # live before trusting this.
            role_click_key = f"roleclick_{channel}"
            selected_idx_key = f"selected_idx_{channel}"

            scroll_pending_key = f"scroll_pending_{channel}"

            def _activate_row(selected_idx_key=selected_idx_key, role_click_key=role_click_key, scroll_pending_key=scroll_pending_key):
                click = st.session_state.get(role_click_key)
                if click:
                    st.session_state[selected_idx_key] = click["row"]
                    st.session_state[scroll_pending_key] = True

            st.dataframe(
                df,
                hide_index=True,
                width="stretch",
                column_config=left_aligned_columns(df, extra={
                    "Posting": st.column_config.LinkColumn("Posting", display_text="Open", alignment="left"),
                    "Role": st.column_config.ButtonColumn(
                        "Role", type="tertiary", alignment="left",
                        on_click=_activate_row, key=role_click_key,
                    ),
                }),
                key=f"table_{channel}",
            )

            # The row-count of `deduped` can shrink between reruns (e.g.
            # moving the compatibility slider re-filters `ranked`/`deduped`
            # this same run) - a previously selected index can point past
            # the end of the new, shorter list. Bounds-check rather than
            # crash; a selection that no longer exists just shows no detail
            # panel, which is correct since that row may no longer be
            # visible at all.
            selected_idx = st.session_state.get(selected_idx_key)
            selected_rows = [selected_idx] if selected_idx is not None and selected_idx < len(deduped) else []
            if selected_rows:
                job = deduped[selected_rows[0]]
                postings = postings_by_primary[id(job)]

                # Auto-scrolls to the detail panel right after a fresh Role
                # click (Zahir's ask 2026-07-31 - Streamlit always renders
                # the whole page top to bottom, so the panel appears below
                # the table with no built-in way to jump the viewport to
                # it). Gated on scroll_pending_key, which _activate_row only
                # sets on an actual click - not on every rerun caused by
                # interacting with a widget already inside this panel
                # (typing a strategy tag, picking a status), which would
                # otherwise yank the view back up on every keystroke.
                anchor_id = f"detail-anchor-{channel}"
                if st.session_state.pop(scroll_pending_key, False):
                    st.html(
                        f'<div id="{anchor_id}"></div>'
                        '<script>'
                        f'document.getElementById("{anchor_id}")?.scrollIntoView({{behavior: "smooth", block: "start"}});'
                        '</script>',
                        unsafe_allow_javascript=True,
                    )
                else:
                    st.html(f'<div id="{anchor_id}"></div>')

                st.markdown(f"{job.get('location') or 'Location not listed'}")
                if "fit_score" in job:
                    st.markdown(job.get("fit_rationale") or "")
                else:
                    st.markdown("Compatibility: not yet scored")
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
                    ("apply_answers", "Apply Assist packet"),
                ]
                doc_cols = st.columns(len(doc_types))
                checked = {}
                for col, (doc_key, doc_label) in zip(doc_cols, doc_types):
                    with col:
                        checked[doc_key] = st.checkbox(
                            doc_label,
                            value=doc_key in requested,
                            key=f"doc_{doc_key}_{job.get('source')}_{job.get('job_id')}",
                        )
                gen_col, _ = st.columns([1, 3])
                with gen_col:
                    generate_clicked = st.button(
                        "Generate documents",
                        key=f"gendocs_{job.get('source')}_{job.get('job_id')}",
                        type="primary",
                    )
                if not drafting_is_configured():
                    st.markdown(
                        "No Anthropic API key found, so this button will save your "
                        "selection but can't draft the documents yet. Add "
                        "`ANTHROPIC_API_KEY` to the `.env` file in the Panga folder "
                        "(get a key at console.anthropic.com) and restart the app."
                    )
                if generate_clicked:
                    selected = [k for k, v in checked.items() if v]
                    if not selected:
                        st.toast("Check at least one document type first.", icon=":material/warning:")
                    elif not drafting_is_configured():
                        upsert_application(job["source"], job["job_id"], status="under review", documents_requested=selected)
                        st.toast("Saved your selection - add an API key to actually draft the documents.", icon=":material/info:")
                        st.rerun()
                    else:
                        doc_labels = dict(doc_types)
                        progress_bar = st.progress(0, text=f"Drafting 1 of {len(selected)}: {doc_labels[selected[0]]}...")

                        def _update_progress(i, total, doc_key, substatus=None):
                            label = f"Drafting {i} of {total}: {doc_labels[doc_key]}"
                            label += f" — {substatus}" if substatus else "..."
                            progress_bar.progress((i - 1) / total, text=label)

                        try:
                            drafted = generate_documents(job, load_profile(), selected, on_progress=_update_progress)
                        except (DraftingNotConfigured, DraftingFailed) as exc:
                            progress_bar.empty()
                            st.error(str(exc))
                        else:
                            progress_bar.progress(1.0, text="Done.")
                            resume_draft = drafted.get("resume")
                            resume_is_scored = isinstance(resume_draft, dict)
                            upsert_application(
                                job["source"], job["job_id"], status="under review",
                                documents_requested=selected,
                                resume_text=resume_draft["text"] if resume_is_scored else resume_draft,
                                resume_ats_score=resume_draft["ats_score"] if resume_is_scored else None,
                                resume_ats_rationale=resume_draft["ats_rationale"] if resume_is_scored else None,
                                resume_ats_next_actions=resume_draft["ats_next_actions"] if resume_is_scored else None,
                                resume_clarifying_questions=resume_draft["clarifying_questions"] if resume_is_scored else None,
                                suggested_strategy_tag=resume_draft["suggested_strategy_tag"] if resume_is_scored else None,
                                cover_letter_text=drafted.get("cover_letter"),
                                exec_bio_text=drafted.get("exec_bio"),
                                leadership_summary_text=drafted.get("leadership_summary"),
                                apply_answers=drafted.get("apply_answers"),
                            )
                            sync_workspace_documents(job["source"], job["job_id"], selected, drafted, load_profile(), job)
                            st.toast("Documents drafted. Review and download them below, then use them for the actual application.", icon=":material/check_circle:")
                            st.rerun()

                doc_field_map = {
                    "resume": "resume_text",
                    "cover_letter": "cover_letter_text",
                    "exec_bio": "exec_bio_text",
                    "leadership_summary": "leadership_summary_text",
                    "apply_answers": "apply_answers",
                }
                for doc_key, doc_label in doc_types:
                    drafted_text = app_record.get(doc_field_map[doc_key])
                    if doc_key == "apply_answers":
                        if drafted_text:
                            with st.expander(f"{doc_label} (drafted)"):
                                st.markdown(
                                    "Open the real application yourself and paste each "
                                    "answer below into the matching field - nothing here "
                                    "is submitted automatically."
                                )
                                for item in drafted_text:
                                    label = item.get("label", "")
                                    value = item.get("value", "")
                                    st.markdown(label)
                                    st.code(value, language=None, wrap_lines=True)
                        continue
                    if drafted_text:
                        with st.expander(f"{doc_label} (drafted)"):
                            if doc_key == "resume" and app_record.get("resume_ats_score") is not None:
                                with st.container(border=True):
                                    current_score = app_record["resume_ats_score"]
                                    prev_score_key = f"prev_ats_score_{job.get('source')}_{job.get('job_id')}"
                                    # Set right before a regenerate call below, popped (shown once,
                                    # not left sitting on every future page view) here on the very
                                    # next render after that regeneration completes - st.metric's
                                    # native delta arrow is the "look nice" version of "show the
                                    # recalculated score" Zahir asked for, not just the bare new number.
                                    score_delta = None
                                    if prev_score_key in st.session_state:
                                        prev_score = st.session_state.pop(prev_score_key)
                                        if prev_score is not None and prev_score != current_score:
                                            score_delta = current_score - prev_score
                                    st.metric("ATS compatibility score", f"{current_score}/100", delta=score_delta)
                                    st.markdown(f"**Why this score:** {app_record.get('resume_ats_rationale') or ''}")
                                    next_actions = app_record.get("resume_ats_next_actions") or []
                                    if next_actions:
                                        st.markdown("**How to raise it:**")
                                        for action in next_actions:
                                            st.markdown(f"- {action}")
                                    clarifying_questions = app_record.get("resume_clarifying_questions") or []
                                    if clarifying_questions:
                                        st.markdown(
                                            "**Answer these to raise the score further - only used if you "
                                            "confirm they're true, nothing is ever invented:**"
                                        )
                                        st.markdown(
                                            "Some boxes are pre-filled with a proposed guess (worded "
                                            "as a guess, e.g. \"Roughly 8-10 engineers?\") - edit it "
                                            "to whatever's actually true, or clear it if it's wrong. "
                                            "Nothing pre-filled is saved as-is."
                                        )
                                        job_key = f"{job.get('source')}_{job.get('job_id')}"
                                        answer_inputs = {}
                                        for q in clarifying_questions:
                                            # Keyed by the question's own content, not its position
                                            # (qi) - a real bug found 2026-07-31: each regeneration
                                            # round produces a differently-worded, differently-
                                            # ordered set of questions, but Streamlit persists a
                                            # text_area's value across reruns by its key. Keying by
                                            # position meant a NEW round's box at the same position
                                            # silently kept the PREVIOUS round's leftover answer text
                                            # for a completely different question, which then got
                                            # saved under the new (wrong) skill label - confirmed in
                                            # gap_interview_answers with several mismatched entries.
                                            # Also folded in the suggested_answer text (2026-07-31,
                                            # same bug class caught on the strategy-tag box): the SAME
                                            # question can recur across rounds with a DIFFERENT
                                            # suggested answer, and a key on question text alone would
                                            # silently keep showing the earlier round's stale suggestion.
                                            q_key = f"gapans_{job_key}_{abs(hash(q['question'] + '|' + (q.get('suggested_answer') or ''))) % 10_000_000}"
                                            answer_inputs[q["skill"]] = st.text_area(
                                                q["question"], value=q.get("suggested_answer") or "",
                                                key=q_key, height=68,
                                            )
                                        if st.button("Save answers & regenerate resume", key=f"gapsave_{job_key}"):
                                            answered = {skill: ans for skill, ans in answer_inputs.items() if ans and ans.strip()}
                                            if not answered:
                                                st.toast("Answer at least one question first.", icon=":material/warning:")
                                            else:
                                                st.session_state[prev_score_key] = current_score
                                                save_gap_answers(job, answered)
                                                regen_bar = st.progress(0, text="Regenerating resume with your answers...")

                                                def _update_regen_progress(i, total, doc_key2, substatus=None):
                                                    label = "Regenerating resume"
                                                    label += f" — {substatus}" if substatus else "..."
                                                    regen_bar.progress((i - 1) / total, text=label)

                                                try:
                                                    regen = generate_documents(
                                                        job, load_profile(), ["resume"], on_progress=_update_regen_progress,
                                                    )
                                                except (DraftingNotConfigured, DraftingFailed) as exc:
                                                    regen_bar.empty()
                                                    st.error(str(exc))
                                                else:
                                                    regen_bar.progress(1.0, text="Done.")
                                                    new_resume = regen["resume"]
                                                    upsert_application(
                                                        job["source"], job["job_id"], status="under review",
                                                        resume_text=new_resume["text"],
                                                        resume_ats_score=new_resume["ats_score"],
                                                        resume_ats_rationale=new_resume["ats_rationale"],
                                                        resume_ats_next_actions=new_resume["ats_next_actions"],
                                                        resume_clarifying_questions=new_resume["clarifying_questions"],
                                                        suggested_strategy_tag=new_resume["suggested_strategy_tag"],
                                                    )
                                                    sync_workspace_documents(
                                                        job["source"], job["job_id"], ["resume"],
                                                        {"resume": new_resume["text"]}, load_profile(), job,
                                                    )
                                                    st.toast(
                                                        f"Resume regenerated - new ATS score {new_resume['ats_score']}/100.",
                                                        icon=":material/check_circle:",
                                                    )
                                                    st.rerun()
                            if doc_key == "resume" and app_record.get("resume_ats_score") is not None:
                                st.markdown(f"**Resume text (ATS score: {app_record['resume_ats_score']}/100):**")
                            st.code(drafted_text, language=None, wrap_lines=True)
                            # No download button here on purpose (removed
                            # 2026-07-31, real bug Zahir hit): sync_workspace_
                            # documents() already wrote this exact .docx into
                            # the per-application folder below the moment it
                            # was drafted. A browser download button would
                            # save a SECOND copy to Chrome's Downloads folder
                            # - if Zahir edited that copy instead of the
                            # workspace one, check_for_edits() would silently
                            # see "no changes" on the file it actually
                            # tracks, since it never looks at Downloads. One
                            # editable copy, in the folder shown below, is
                            # the only correct place to make changes.

                has_any_document = any(
                    app_record.get(f) for f in ("resume_text", "cover_letter_text", "exec_bio_text", "leadership_summary_text")
                )
                job_key = f"{job.get('source')}_{job.get('job_id')}"
                if has_any_document:
                    workspace_folder = dossier_dir(job.get("source"), job.get("job_id"), job.get("organization"), job.get("title"))
                    st.markdown("**Your application folder (edit the Word files directly here):**")
                    st.code(str(workspace_folder), language=None)
                    st.markdown(
                        "Open this folder in File Explorer and edit the .docx files directly in Word "
                        "if you want to change anything (each file is named "
                        "Name_DocType_Role_Company.docx, same convention as before), then click "
                        "\"Check my edited documents\" below - you'll need to do this before marking "
                        "the job \"applied\"."
                    )
                    if st.button("Check my edited documents", key=f"checkedits_{job_key}"):
                        st.session_state[f"editreport_{job_key}"] = check_for_edits(job["source"], job["job_id"])

                    # edit_report/what_changed are computed here (if a check
                    # has been run) but RENDERED further below, folded into
                    # the single combined save row - kept apart from that
                    # rendering so the diff display above stays exactly
                    # where Zahir expects it, right under the "Check my
                    # edited documents" button.
                    report_key = f"editreport_{job_key}"
                    edit_report = st.session_state.get(report_key)
                    what_changed = ""
                    if edit_report:
                        any_changed = any(r.get("changed") for r in edit_report.values())
                        missing_files = [k for k, r in edit_report.items() if r.get("no_workspace_file")]
                        if any_changed:
                            st.markdown("**Changes found in your working copies:**")
                            for doc_key2, result in edit_report.items():
                                if result.get("changed"):
                                    with st.expander(f"{doc_key2} - changed"):
                                        st.code("\n".join(result["diff"]), language=None, wrap_lines=True)
                        elif not missing_files:
                            st.markdown("No changes found - your working copies match what was drafted.")
                        if missing_files:
                            st.markdown(
                                f"No editable file found yet for: {', '.join(missing_files)} "
                                "(drafted before this folder feature existed, or not regenerated since) - "
                                "click \"Generate documents\" above to create it, or note in the reason "
                                "box below that you're using the downloaded copy instead."
                            )
                        changed_docs = [k for k, r in edit_report.items() if r.get("changed")]
                        if changed_docs:
                            parts = []
                            for doc_key2 in changed_docs:
                                diff_lines = edit_report[doc_key2]["diff"]
                                added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
                                removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
                                parts.append(f"{doc_key2}: {added} line(s) added, {removed} removed")
                            what_changed = "; ".join(parts) + "."
                else:
                    edit_report, what_changed = None, ""

                if status in ("applied", "interview scheduled"):
                    if st.button("Prep for interview", key=f"prep_results_{job.get('source')}_{job.get('job_id')}"):
                        go_to_prep({
                            "kind": "job",
                            "source": job["source"],
                            "job_id": job["job_id"],
                            "job_label": job_label(job),
                        })

                # Why-reason, Strategy tag, and Mark status side by side,
                # one shared "Save status" button underneath (Zahir's
                # explicit ask 2026-07-31) - these three used to be three
                # separate decisions with three separate buttons (Save
                # review/Save tag/Save status); they're really all made at
                # the same moment, so now one action saves all three.
                reason_col, tag_col, status_col = st.columns(3)
                report_hash = abs(hash(str(edit_report))) % 10_000_000 if edit_report else 0
                with reason_col:
                    # "Why" only ever gets a placeholder, never a prefilled
                    # guess (see history above the removed block) - the
                    # whole point is Zahir's own reasoning, not the app's.
                    why_reason = st.text_area(
                        "Why did you make these changes? (required before marking applied)",
                        placeholder="e.g. Simplified the bullet formatting so it parses cleanly in the ATS.",
                        key=f"editreason_{job_key}_{report_hash}", height=68,
                    )
                    if what_changed:
                        st.markdown(f"What changed: {what_changed}")
                with tag_col:
                    # Prefilled from the resume draft's own proposed tag
                    # (Zahir's ask 2026-07-31) whenever he hasn't saved a
                    # real one yet. Keyed on a hash of the suggestion text
                    # itself, not just job_key - a real bug caught live:
                    # a stable key meant Streamlit kept showing the box's
                    # ORIGINAL (blank, pre-suggestion) session state
                    # forever, silently ignoring every new `value=` from a
                    # later regenerate. Same fix already applied to the
                    # gap-answer and why-reason boxes above.
                    tag_default = app_record.get("strategy_tag") or app_record.get("strategy_tag_suggestion") or ""
                    tag_version = abs(hash(tag_default)) % 10_000_000
                    new_tag = st.text_input(
                        "Strategy tag (optional - what's different about this draft, e.g. \"concise-1-page\")",
                        value=tag_default, key=f"tag_{job.get('source')}_{job.get('job_id')}_{tag_version}",
                    )
                with status_col:
                    new_status = st.selectbox(
                        "Mark status",
                        ["-", "applied", "interview scheduled", "offer", "rejected", "not interested", "save for later"],
                        key=f"status_{job.get('source')}_{job.get('job_id')}",
                    )
                    skip_reason = None
                    if new_status == "not interested":
                        skip_reason = st.text_area(
                            "Why not interested? (optional)",
                            key=f"reason_{job.get('source')}_{job.get('job_id')}", height=68,
                        )

                if st.button("Save status", key=f"save_status_{job.get('source')}_{job.get('job_id')}"):
                    if new_status == "-":
                        st.toast("Pick a status first.", icon=":material/warning:")
                    elif new_status == "applied" and needs_edit_review(app_record) and not why_reason.strip():
                        st.error(
                            "Check your edited documents (button above) and explain why in the "
                            "reason box before marking this applied."
                        )
                    else:
                        if why_reason.strip():
                            full_reason = f"{what_changed} Why: {why_reason.strip()}" if what_changed else why_reason.strip()
                            record_document_edit_review(job["source"], job["job_id"], edit_report or {}, full_reason)
                        if new_tag != (app_record.get("strategy_tag") or ""):
                            set_strategy_tag(job["source"], job["job_id"], new_tag)
                        upsert_application(job["source"], job["job_id"], status=new_status, skip_reason=skip_reason)
                        st.toast("Saved.", icon=":material/check_circle:")
                        st.rerun()
                st.divider()
                render_outreach_section(f"job_{job.get('source')}_{job.get('job_id')}", job_source=job.get("source"), job_id=job.get("job_id"))

elif active_tab == "prospector":
    render_feedback_widget("prospector")

    st.header("Prospector")
    st.markdown(
        "Companies worth watching before they've posted a role, outreach logging, coverage/"
        "activity/outcome numbers, and cross-cutting insights (PRD §16/§17) from your job search."
    )

    settings = load_settings()
    target_roles = settings.get("target_roles", [])
    applications = load_applications()
    target_accounts = load_target_accounts()
    outreach_records = load_outreach()

    st.subheader("Prospector Score")
    st.markdown(
        "One headline number for how well your proactive search is working - "
        "target accounts, outreach, and real outcomes combined. This is "
        "self-learning by design: Claude recomputes it by reasoning over "
        "your actual results rather than a fixed formula, so it gets "
        "sharper as more real outcomes accumulate instead of staying static."
    )
    prospector_score = load_prospector_score()
    with st.container(border=True):
        if prospector_score["score"] is None:
            st.info("Not yet computed - click \"Compute Prospector Score\" below.")
        else:
            st.metric("Prospector Score", f"{prospector_score['score']}/100")
            st.markdown("**Why this score:**")
            st.markdown(prospector_score["rationale"])
            if prospector_score.get("next_actions"):
                st.markdown("**How to raise it:**")
                for action in prospector_score["next_actions"]:
                    st.markdown(f"- {action}")
            st.markdown(f"Based on {prospector_score['data_points']} real outcome data point(s) as of {format_timestamp(prospector_score['computed_at'])} - recompute anytime, it re-reads your actual data fresh so real progress is reflected automatically.")
    # Computes for real on click (Zahir's explicit ask 2026-07-31: the old
    # two-step "Prepare data" -> "go ask Claude Code" flow was the exact
    # same friction point already fixed once for document drafting - see
    # tailoring/drafting.py's module docstring). Same deliberate, narrow
    # direct-API exception, applied here for the same reason.
    if st.button("Compute Prospector Score", type="primary"):
        score_input = gather_prospector_score_input(
            applications, jobs, target_accounts, outreach_records, prep_records, target_roles,
        )
        # Real streaming progress instead of a spinner (Zahir's explicit
        # ask 2026-07-31) - same thinking/writing character-count
        # mechanism as document drafting.
        score_compute_bar = st.progress(0, text="Reasoning over your real data...")

        def _update_score_compute_progress(substatus):
            score_compute_bar.progress(0.5, text=f"Reasoning over your real data - {substatus}")

        try:
            result = compute_prospector_score(score_input, on_progress=_update_score_compute_progress)
        except (DraftingNotConfigured, DraftingFailed) as exc:
            score_compute_bar.empty()
            st.error(str(exc))
        else:
            score_compute_bar.progress(1.0, text="Done.")
            save_prospector_score(
                result["score"], result["rationale"], result["next_actions"],
                score_input["data_points"], datetime.now(timezone.utc).isoformat(),
            )
            st.toast(f"Prospector Score: {result['score']}/100.", icon=":material/check_circle:")
            st.rerun()

    st.subheader("Target accounts")
    st.markdown(
        "Companies worth watching before they've posted a role - currently pharma-only "
        "(other industries need their own good-signal criteria worked out with Zahir first, "
        "not assumed). Sourced from ClinicalTrials.gov Phase 3 trial activity, commercial-build "
        "hiring postings already in Results, and SEC S-1/IPO filings mentioning Phase 3 activity - "
        "all APPROACHING-commercialization signals on purpose (2026-07-31: an 'already approved' "
        "signal was removed after real review showed it was surfacing companies years past their "
        "actual hiring window, not before it - see regulatory_filings.py). Late Phase 3/PDUFA-date/"
        "just-submitted-NDA detection isn't built yet - openFDA has no reliable data for that; it "
        "would need a different source (e.g. SEC filings mentioning \"PDUFA\"), not yet built. "
        "Filtered to exclude obvious non-companies (universities, hospitals, government), known "
        "mega-pharma majors, and known-acquired companies - but not every remaining entry is a "
        "great fit (a research consortium, an unusual NDA holder, or an unrelated industry whose "
        "job title happened to match can still slip through), so treat \"watching\" as a starting "
        "point to review, not a verified lead. \"Signals\" below counts DISTINCT signal types "
        "found for that company (2+ distinct types auto-promotes it to \"qualified\"; 1 stays "
        "\"watching\") - click a company to see exactly what each one is. Mark anything wrong as "
        "\"disqualified\"."
    )
    if not target_accounts:
        st.info("No target accounts yet.")
    else:
        # Disqualified/stale accounts previously stayed visible forever once
        # marked - Zahir's real complaint 2026-07-31: he'd already
        # disqualified UroGen (bad signal, see note above) but it was still
        # sitting in the list looking untouched, since "disqualified" only
        # changed the Status column text, nothing hid it. Same "hidden by
        # default, nothing deleted" pattern as the Results tab's "not
        # interested" jobs toggle.
        DONE_STATUSES = ("disqualified", "stale")
        hidden_count = sum(1 for a in target_accounts if a["status"] in DONE_STATUSES)
        show_done = st.checkbox(
            f"Show {hidden_count} disqualified/stale account(s) (hidden by default, nothing is deleted)"
        ) if hidden_count else False
        visible_accounts = target_accounts if show_done else [a for a in target_accounts if a["status"] not in DONE_STATUSES]

        # Website URLs aren't in any signal source - they need a real web
        # lookup (Zahir's explicit ask 2026-07-31), same one-time-search-
        # then-cache pattern as the cover letter's company-address lookup.
        # A real API call per company, so this is an explicit button (cost
        # visible, under his control) rather than something that fires
        # silently on every page load.
        missing_website = [a for a in visible_accounts if "website" not in a]
        # Real bug found live 2026-07-31: the last-run cost line lived
        # INSIDE `if missing_website:` alongside the button - once a batch
        # resolved every remaining account, missing_website went empty, the
        # whole block (button AND cost line) stopped rendering, and the
        # cost Zahir had just spent seemed to vanish. Show the last-run
        # summary unconditionally (whenever a run has ever happened); only
        # the button itself is conditional on there being work left to do.
        last_run = load_website_lookup_cost()
        if last_run["count"]:
            st.markdown(f"Last website lookup: ${last_run['cost']:.2f} for {last_run['count']} account(s) as of {format_timestamp(last_run['at'])}.")
        if missing_website:
            cost_label = f"(${last_run['cost']:.2f} for the last run)" if last_run["count"] else "(real API cost)"
            if st.button(f"Look up website for {len(missing_website)} account(s) {cost_label}"):
                # Real "i of N" progress instead of a spinner (Zahir's
                # explicit ask 2026-07-31) - one company per real search
                # call, so this is a genuine count, not a simulated one.
                total = len(missing_website)
                lookup_bar = st.progress(0, text=f"Looking up website 1 of {total}: {missing_website[0]['company_name']}...")
                run_cost = 0.0
                for i, acc in enumerate(missing_website, start=1):
                    lookup_bar.progress((i - 1) / total, text=f"Looking up website {i} of {total}: {acc['company_name']}...")
                    found, cost = lookup_company_website(acc["company_name"])
                    set_website(acc["company_name"], found or "")
                    run_cost += cost
                lookup_bar.progress(1.0, text="Done.")
                save_website_lookup_cost(run_cost, total)
                st.rerun()

        ta_rows = [{
            "Company": a["company_name"],
            "Website": a.get("website") or "",
            "Status": a["status"],
            # A bare count ("2") wasn't readable at a glance (Zahir's
            # explicit ask 2026-07-31) - a short comma-joined list of what
            # those signals actually are is self-explanatory without a
            # click, same spirit as the human labels used in the detail
            # panel below.
            "Signals": ", ".join(SIGNAL_TYPE_LABELS.get(s["signal_type"], s["signal_type"]) for s in a["signals"]) or "-",
            "Industry": a.get("industry") or "-",
        } for a in visible_accounts]
        ta_df = pd.DataFrame(ta_rows)
        # Same click-to-activate pattern as the Results tab's Role column
        # (Zahir's explicit ask 2026-07-31, "i want the same behaviour like
        # the job page") - no checkbox row-selector, click the Company cell
        # itself. See the Results tab's matching block for the mechanism
        # notes (ButtonColumn on_click + a scroll-into-view anchor).
        ta_click_key = "ta_company_click"
        ta_selected_key = "ta_selected_idx"
        ta_scroll_key = "ta_scroll_pending"

        def _activate_ta_row():
            click = st.session_state.get(ta_click_key)
            if click:
                st.session_state[ta_selected_key] = click["row"]
                st.session_state[ta_scroll_key] = True

        st.dataframe(
            ta_df, hide_index=True, width="stretch", key="target_accounts_table",
            column_config=left_aligned_columns(ta_df, extra={
                "Company": st.column_config.ButtonColumn(
                    "Company", type="tertiary", alignment="left",
                    on_click=_activate_ta_row, key=ta_click_key,
                ),
                "Website": st.column_config.LinkColumn("Website", display_text="Visit", alignment="left"),
            }),
        )
        selected_ta_idx = st.session_state.get(ta_selected_key)
        selected_ta_rows = [selected_ta_idx] if selected_ta_idx is not None and selected_ta_idx < len(visible_accounts) else []
        if selected_ta_rows:
            acc = visible_accounts[selected_ta_rows[0]]
            ta_anchor_id = "detail-anchor-target-accounts"
            if st.session_state.pop(ta_scroll_key, False):
                st.html(
                    f'<div id="{ta_anchor_id}"></div>'
                    '<script>'
                    f'document.getElementById("{ta_anchor_id}")?.scrollIntoView({{behavior: "smooth", block: "start"}});'
                    '</script>',
                    unsafe_allow_javascript=True,
                )
            else:
                st.html(f'<div id="{ta_anchor_id}"></div>')
            st.markdown(f"**{acc['company_name']}**")
            for sig in acc["signals"]:
                sig_label = SIGNAL_TYPE_LABELS.get(sig["signal_type"], sig["signal_type"])
                st.markdown(f"[{sig_label}, {sig['source']}] {sig['detail']} (observed {sig['date_observed'][:10]})")
            if acc.get("notes"):
                st.markdown(f"Notes: {acc['notes']}")
            new_ta_status = st.selectbox(
                "Status", ["watching", "qualified", "contacted", "stale", "disqualified"],
                index=["watching", "qualified", "contacted", "stale", "disqualified"].index(acc["status"]),
                key=f"ta_status_{acc['company_name']}",
            )
            ta_notes = st.text_area("Notes (optional)", value=acc.get("notes") or "", key=f"ta_notes_{acc['company_name']}")
            if st.button("Save", key=f"ta_save_{acc['company_name']}"):
                set_target_account_status(acc["company_name"], new_ta_status, notes=ta_notes or None)
                st.toast("Saved.", icon=":material/check_circle:")
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
    coverage_by_channel_df = pd.DataFrame(sorted(coverage["by_channel"].items()), columns=["Channel", "Jobs"])
    st.dataframe(
        coverage_by_channel_df,
        hide_index=True, width="content", column_config=left_aligned_columns(coverage_by_channel_df),
    )
    if coverage["untimestamped"]:
        st.markdown(f"{coverage['untimestamped']} job(s) predate 2026-07-30 and have no discovery date, so they're in the total but not the 7-day count.")

    st.subheader("Activity")
    a1, a2 = st.columns(2)
    a1.metric("Applications tracked (total)", activity["total_applications"])
    a2.metric("Started in last 7 days", activity["created_last_7_days"])
    activity_by_status_df = pd.DataFrame(sorted(activity["by_status"].items()), columns=["Status", "Count"])
    st.dataframe(
        activity_by_status_df,
        hide_index=True, width="content", column_config=left_aligned_columns(activity_by_status_df),
    )
    if activity["untimestamped"]:
        st.markdown(f"{activity['untimestamped']} application(s) predate 2026-07-30 and have no start date, so they're in the total but not the 7-day count.")

    st.subheader("Outcome")
    st.markdown(
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
        st.markdown(f"Based on {overall['applied']} application(s) that reached \"applied\" or later.")

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
            by_channel_df = rates_table(outcome["by_channel"], "Channel")
            st.dataframe(by_channel_df, hide_index=True, width="content", column_config=left_aligned_columns(by_channel_df))
        with st.expander("By fit-score band"):
            by_score_band_df = rates_table(outcome["by_score_band"], "Score band")
            st.dataframe(by_score_band_df, hide_index=True, width="content", column_config=left_aligned_columns(by_score_band_df))
        with st.expander("By target-role priority weight"):
            st.markdown("Weight comes from Settings > target roles - higher weight roles are the ones you prioritized.")
            by_role_weight_df = rates_table(outcome["by_role_weight"], "Priority weight")
            st.dataframe(by_role_weight_df, hide_index=True, width="content", column_config=left_aligned_columns(by_role_weight_df))

    st.subheader("Rejection-pattern diagnosis")
    st.markdown(
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
                    rejected_df = pd.DataFrame(diagnosis_input["rejected"])
                    st.dataframe(rejected_df, hide_index=True, width="stretch", column_config=left_aligned_columns(rejected_df))
                if diagnosis_input["not_interested_with_reason"]:
                    st.markdown("**Not interested (with reason)**")
                    not_interested_df = pd.DataFrame(diagnosis_input["not_interested_with_reason"])
                    st.dataframe(not_interested_df, hide_index=True, width="stretch", column_config=left_aligned_columns(not_interested_df))
                    # (rejected_df/not_interested_df kept at width="stretch",
                    # not "content" - unlike the summary tables above, these
                    # hold free-text rejection reasons/skip notes that need
                    # room to read, not a tight fit.)

    st.subheader("Insights (Learn Engine)")
    st.markdown(
        "Cross-cutting feedback loop over every prediction Panga makes - scoring, target-account "
        "qualification, outreach channel, strategy tags, interview outcomes (PRD §17). Same as "
        "rejection-pattern diagnosis above: this button only gathers the data across all of these "
        "tables - come back to Claude Code and ask it to run the analysis. Recommend-only, always - "
        "it never changes a score threshold or any setting on its own."
    )
    if st.button("Prepare Learn Engine data"):
        st.session_state["learn_engine_input"] = gather_learn_engine_input(
            applications, jobs, target_accounts, outreach_records, prep_records,
        )

    learn_input = st.session_state.get("learn_engine_input")
    if learn_input:
        l1, l2, l3, l4 = st.columns(4)
        l1.metric("Scored applications", len(learn_input["scoring_vs_outcome"]))
        l2.metric("Target accounts", len(learn_input["target_account_vs_outcome"]))
        l3.metric("Outreach records", len(learn_input["outreach_vs_outcome"]))
        l4.metric("Interview outcomes", len(learn_input["interview_outcomes"]))
        total_inputs = sum(len(learn_input[k]) for k in ("scoring_vs_outcome", "target_account_vs_outcome", "outreach_vs_outcome", "interview_outcomes"))
        if total_inputs == 0:
            st.info("Nothing to analyze yet - come back once there's more history across scoring, target accounts, outreach, or interviews.")
        else:
            st.success("Data's ready. Go to Claude Code and ask it to run the Learn Engine analysis.")
            for gap in learn_input["known_gaps"]:
                st.markdown(f"**Known gap:** {gap}")
            with st.expander("What it'll be looking at"):
                for key, title in [
                    ("scoring_vs_outcome", "Scoring vs. application outcome"),
                    ("target_account_vs_outcome", "Target accounts vs. real postings"),
                    ("outreach_vs_outcome", "Outreach vs. response"),
                    ("interview_outcomes", "Interview outcomes"),
                ]:
                    if learn_input[key]:
                        st.markdown(f"**{title}**")
                        learn_key_df = pd.DataFrame(learn_input[key])
                        st.dataframe(learn_key_df, hide_index=True, width="content", column_config=left_aligned_columns(learn_key_df))

elif active_tab == "prep":
    render_feedback_widget("prep")

    st.header("Interview prep")

    prep_target = st.session_state.get("prep_target")
    if prep_target:
        with st.container(border=True):
            if prep_target["kind"] == "job":
                st.markdown(f"**Ready to prep: {prep_target['job_label']}**")
                st.markdown("Go to Claude Code and ask to prep for this interview - it'll research the interviewer(s)/company and draft persona-aware questions and talking points from your master profile.")
            else:
                st.markdown(f"**Ready to prep: \"{prep_target['subject']}\"** from {prep_target['sender']}")
                st.markdown("Go to Claude Code and ask to prep for this interview. It'll read the full email thread first to find interviewer/panel names, match it to the right application, then research from there.")
            if st.button("Clear", key="clear_prep_target"):
                st.session_state["prep_target"] = None
                st.rerun()

    if not prep_records:
        st.markdown("No interview prep started yet. Use \"Prep for this interview\" on Results or Call to Action once you're past the applied stage.")

    for record in prep_records:
        job = next((j for j in jobs if j["source"] == record["source"] and j["job_id"] == record["job_id"]), None)
        label = job_label(job) if job else f"{record['source']} {record['job_id']}"
        st.subheader(label)

        for round_ in record["rounds"]:
            status_note = "in progress" if round_["status"] == "in_progress" else round_["status"]
            with st.expander(f"{round_['round_label']} - {status_note}", expanded=(round_["status"] == "in_progress")):
                logistics = " - ".join(v for v in [round_.get("date"), round_.get("format")] if v)
                if logistics:
                    st.markdown(logistics)

                if round_.get("interviewers"):
                    for person in round_["interviewers"]:
                        st.markdown(f"**{person.get('name')}**" + (f", {person.get('title')}" if person.get("title") else ""))
                        if person.get("research_summary"):
                            st.markdown(person["research_summary"])
                        if person.get("persona"):
                            st.markdown(f"_Likely focus:_ {person['persona']}")
                        for link in person.get("research_links") or []:
                            st.markdown(link)

                if round_.get("company_snapshot"):
                    st.markdown(f"**Company snapshot:** {round_['company_snapshot']}")

                if round_.get("likely_questions"):
                    st.markdown("**Likely questions**")
                    for q in round_["likely_questions"]:
                        asked_by = f" ({q['asked_by']})" if q.get("asked_by") else ""
                        st.markdown(f"- {q.get('question')}{asked_by}")
                        if q.get("why"):
                            st.markdown(q["why"])
                        if q.get("talking_point"):
                            st.markdown(f"  > {q['talking_point']}")

                if round_.get("questions_to_ask"):
                    st.markdown("**Questions to ask them**")
                    for q in round_["questions_to_ask"]:
                        best_for = f" (best for {q['best_for']})" if q.get("best_for") else ""
                        st.markdown(f"- {q.get('question')}{best_for}")

                st.divider()
                OUTCOME_OPTIONS = ["not yet", "went well", "went okay", "went poorly"]
                current_outcome = round_.get("outcome") or "not yet"
                new_outcome = st.selectbox(
                    "How did it go? (PRD §17 - feeds the Learn Engine)", OUTCOME_OPTIONS,
                    index=OUTCOME_OPTIONS.index(current_outcome) if current_outcome in OUTCOME_OPTIONS else 0,
                    key=f"outcome_{record['source']}_{record['job_id']}_{round_['round_label']}",
                )
                outcome_notes = st.text_area(
                    "Notes (optional)", value=round_.get("outcome_notes") or "",
                    key=f"outcome_notes_{record['source']}_{record['job_id']}_{round_['round_label']}",
                )
                if st.button("Save outcome", key=f"save_outcome_{record['source']}_{record['job_id']}_{round_['round_label']}"):
                    record_round_outcome(record["source"], record["job_id"], round_["round_label"], new_outcome, outcome_notes or None)
                    st.toast("Saved.", icon=":material/check_circle:")
                    st.rerun()

elif active_tab == "linkedin":
    render_feedback_widget("linkedin")

    st.header("LinkedIn profile enhancement")
    st.markdown(
        "Uploads for your LinkedIn profile and connections live in Settings "
        "now, alongside your resume and other source documents - one shared "
        "place to manage everything Panga reads from. This tab shows the "
        "analysis and suggestions once you've uploaded there and asked "
        "Claude Code to review your profile - that's where the comparison "
        "against your master profile and target-role skills happens, and "
        "where suggested rewrites get drafted. Suggestions below are yours "
        "to copy and paste into LinkedIn's own edit screens."
    )
    if st.button("Go to Settings to upload/update", icon=":material/upload_file:"):
        st.session_state["active_tab"] = "settings"
        st.rerun()

    linkedin_data = load_linkedin_profile()

    if linkedin_data.get("last_saved"):
        st.markdown(f"Last saved: {linkedin_data['last_saved']} (from {', '.join(linkedin_data.get('source_files', []))})")

    st.divider()

    if linkedin_data.get("last_analyzed"):
        score = linkedin_data.get("profile_strength_score")
        if score is not None:
            with st.container(border=True):
                st.metric("Profile strength", f"{score}/100")
                if linkedin_data.get("profile_strength_rationale"):
                    st.markdown("**Why this score, and what would improve it:**")
                    st.markdown(linkedin_data["profile_strength_rationale"])
        st.markdown(f"Last analyzed: {linkedin_data['last_analyzed']}")

        active_suggestions = get_active_suggestions()
        if not active_suggestions:
            st.markdown("No open suggestions - everything's either applied or dismissed.")
        for section in LINKEDIN_SECTIONS:
            section_suggestions = [s for s in active_suggestions if s["section"] == section]
            if not section_suggestions:
                continue
            st.subheader(LINKEDIN_SECTION_LABELS[section])
            for s in section_suggestions:
                if s.get("rationale"):
                    st.markdown(s["rationale"])
                st.markdown("**Suggested text (copy this into LinkedIn):**")
                st.code(s["suggested_text"], language=None, wrap_lines=True)
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
        st.markdown("Not yet analyzed - upload your profile PDF(s) in Settings, then ask Claude to analyze it.")

    st.divider()
    st.subheader("Connections (for Prospector outreach)")
    st.markdown(
        "Connections whose title looks like a recruiter, and connections "
        "who work at a company already in your target accounts list - "
        "upload your connections export in Settings to populate this."
    )
    connections_snapshot = load_connections_snapshot()
    if not connections_snapshot.get("last_saved"):
        st.markdown("No connections uploaded yet - add them in Settings.")
    else:
        conns = connections_snapshot["connections"]
        st.markdown(f"Last saved: {connections_snapshot['last_saved']} ({len(conns)} connections from {connections_snapshot.get('source_file')})")

        recruiters = [c for c in conns if looks_like_recruiter(c.get("position"))]
        target_names = [a["company_name"] for a in load_target_accounts()]
        target_matches = cross_reference_target_accounts(conns, target_names)

        rc1, rc2 = st.columns(2)
        rc1.metric("Recruiter connections", len(recruiters))
        rc2.metric("Connections at a target account", len(target_matches))

        if recruiters:
            with st.expander("Recruiter connections"):
                recruiters_df = pd.DataFrame(recruiters)[["first_name", "last_name", "company", "position"]]
                st.dataframe(recruiters_df, hide_index=True, width="stretch", column_config=left_aligned_columns(recruiters_df))
        if target_matches:
            with st.expander("Connections at a target account"):
                target_matches_df = pd.DataFrame(target_matches)[["first_name", "last_name", "company", "position", "matched_target_account"]]
                st.dataframe(target_matches_df, hide_index=True, width="stretch", column_config=left_aligned_columns(target_matches_df))

elif active_tab == "support":
    render_feedback_widget("support")
    render_support_page(
        BHANGI_PROJECT,
        intro=(
            "Ran into something broken? Describe it below and attach a "
            "screenshot and/or a log file if you have one - this gets queued "
            "for review, same as everything else Panga tracks."
        ),
        project_root=PROJECT_ROOT,
    )
