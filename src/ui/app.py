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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from debug_log import setup_debug_logging
setup_debug_logging()

import extension_bridge
extension_bridge.start_server()


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
from search.job_sources import load_job_sources, save_job_sources
from search.job_alert_senders import load_job_alert_senders, save_job_alert_senders
from search.aggregators import ADZUNA_COUNTRIES, is_configured as adzuna_is_configured
from search.source_activity import all_tracked_sources, is_source_stale
from ranking.prioritize import weight_for, dedupe_across_sources
from tailoring.applications import load_applications, upsert_application, get_application, get_pending_status_suggestions, confirm_status_suggestion, set_strategy_tag, needs_edit_review, record_document_edit_review, get_applications_with_open_clarifying_questions
from tailoring.cta_emails import get_active_cta_emails, request_archive, request_draft, get_awaiting_draft_send
from tailoring.interview_prep import load_interview_prep, get_interview_prep, record_round_outcome, build_prep_context, generate_prep, start_round, save_round
from tailoring.dossier import dossier_dir, sync_workspace_documents, check_for_edits
from tailoring.ats_score import detect_matched_keyword_regressions
from tailoring.drafting import generate_documents, score_job, save_gap_answers, generate_target_roles, is_configured as drafting_is_configured, DraftingNotConfigured, DraftingFailed, analyze_fit_before_drafting as _analyze_fit_before_drafting, check_regenerate_impact as _check_regenerate_impact
from prospector.kpis import coverage_summary, activity_summary, outcome_summary
from prospector.rejection_diagnosis import gather_diagnosis_input, diagnose
from prospector.target_accounts import load_target_accounts, set_status as set_target_account_status, set_website, load_website_lookup_cost, save_website_lookup_cost, find_disqualified_with_new_activity
from prospector.company_lookup import lookup_company_website
from prospector.outreach import (
    add_outreach, update_status as update_outreach_status, request_draft,
    get_outreach_for_target_account, get_outreach_for_job, load_outreach,
    set_strategy_tag as set_outreach_strategy_tag,
)
from prospector.learn_engine import gather_learn_engine_input, analyze as analyze_learn_engine
from prospector.prospector_score import load_prospector_score, gather_prospector_score_input, compute_prospector_score, save_prospector_score
from linkedin.storage import load_linkedin_profile, save_snapshot, save_analysis, mark_suggestion_status, get_active_suggestions, SECTIONS as LINKEDIN_SECTIONS
from linkedin.enhance import build_enhancement_context, analyze_profile
from linkedin.ingest import extract_text_from_pdf
from linkedin.connections import parse_connections_csv, looks_like_recruiter, cross_reference_target_accounts
from linkedin.connections_store import load_connections_snapshot, save_connections
from security.crypto_store import has_recovery_code, generate_recovery_code
import gmail_client
import imap_client
import microsoft_client
import google_calendar_client
from email_providers import detect_imap_settings
from fulfillment import get_last_synced_at, get_pending_count, run_full_fulfillment
from feedback.ui_feedback import get_open_feedback, mark_resolved
from ui.feedback_widget import render_feedback_widget
from profile.ingest import load_manifest_result, remove_document, ingest_uploaded_document, resume_text as ingested_resume_text
from profile.storage import load_profile, update_profile_field
try:
    # Bhangi is a separate, standalone cross-project tool (see
    # _find_bhangi_src above) - not something this app ships or installs
    # itself, so a checkout that doesn't have a sibling Bhangi project (any
    # friend-testing package, for instance - see run_app_friend_test.bat)
    # previously hard-crashed the WHOLE app on this single import. Missing
    # Bhangi should only mean "no Support tab", never "no app at all".
    from bhangi.ui import render_support_page
    from bhangi.issues import create_issue as bhangi_create_issue
    from bhangi.build_info import detect_build as bhangi_detect_build
except ImportError:
    render_support_page = None
    bhangi_create_issue = None
    bhangi_detect_build = None
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

# Every st.button(..., icon=...) in this app pairs the icon with a real,
# non-empty text label (tab nav, "Generate my target roles...", "Open
# folder", etc.) - the icon is always decorative reinforcement, never the
# button's only content. But st.button's icon= parameter renders as a bare
# <span data-testid="stIconMaterial">notifications_active</span> with no
# aria-hidden/aria-label at all, and the <button> itself ships an explicit
# but EMPTY aria-label="" (confirmed by inspecting the real DOM). Verified
# directly against Chrome's own accessibility tree (not just guessed from
# the ARIA spec text) that an empty aria-label doesn't fall through to the
# button's visible text the way a *missing* aria-label would - Chrome
# treats the attribute's mere presence as authoritative and computes an
# empty accessible name. First pass here only added aria-hidden to the
# icon span and left that empty aria-label alone - re-checked the
# accessibility tree afterward (this file's "verify against real data"
# habit, not just "looks right in code") and found every tab button had
# gone from a garbled name ("notifications_activeCall to action (7)",
# Mirror's original finding) to NO name at all, which is worse. Fixing
# both in the same pass: hide the redundant icon from assistive tech AND
# set a real aria-label on the button from its own visible text, so the
# empty one Streamlit ships never gets a chance to win.
#
# Diverges from Mirror's literal suggestion (copy task_alt's role="img" +
# aria-label pattern) on purpose: task_alt is a *standalone* icon with no
# adjacent text, where labeling the icon itself is the right fix. These
# icons duplicate a visible label right next to them - labeling the icon
# too would double-announce ("notifications_active icon, Call to action,
# 7 items"). Labeling the button directly (from its own text) gets to
# "Call to action, 7 items" with nothing redundant. Flagged back to
# Mirror/hub for review since it departs from the literal ask.
#
# Can't pass either attribute through the icon= parameter (Python-level
# API, no such option) - patches the DOM after the fact instead, same
# technique already used elsewhere in this file for the scroll-into-view
# anchor. A MutationObserver (not a one-shot query) because Streamlit
# re-renders these buttons on every tab switch/interaction; the guard flag
# makes re-running this script block on every rerun a no-op after the
# first real pass.
st.html(
    """
    <script>
    (function () {
        function fixDecorativeIconButtons(root) {
            root.querySelectorAll('[data-testid="stIconMaterial"]').forEach(function (icon) {
                var btn = icon.closest('button');
                var labelEl = btn && btn.querySelector('[data-testid="stMarkdownContainer"]');
                if (!labelEl) return;
                icon.setAttribute('aria-hidden', 'true');
                var labelText = labelEl.textContent.trim();
                if (labelText) btn.setAttribute('aria-label', labelText);
            });
        }
        fixDecorativeIconButtons(document);
        if (window.__pangaIconA11yObserver) return;
        var observer = new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
                m.addedNodes.forEach(function (n) {
                    if (n.nodeType === 1) fixDecorativeIconButtons(n);
                });
            });
        });
        observer.observe(document.body, {childList: true, subtree: true});
        window.__pangaIconA11yObserver = observer;
    })();
    </script>
    """,
    unsafe_allow_javascript=True,
)


def load_settings() -> dict:
    return yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")


def application_status(job: dict) -> str | None:
    app = get_application(job.get("source"), job.get("job_id"))
    return app["status"] if app else None


def job_label(job: dict) -> str:
    return f"{job.get('title')} - {job.get('organization')}"


# Shared with the Results tab's own filtering pipeline below, so a status
# string typo can't make the two silently disagree about which jobs count
# as hidden-by-default.
NOT_INTERESTED_STATUSES = ("not interested", "not-interested")  # handle both forms - see applications.py note
CLOSED_STATUSES = ("closed by employer",)
APPLIED_STATUSES = ("applied",)


def compute_results_ranked(
    jobs: list[dict], target_roles: list[dict], min_score: int,
    show_not_interested: bool, show_closed: bool, show_applied: bool,
) -> list[dict]:
    """The same rank/filter/dedup pipeline the Results tab's own "N job(s)"
    heading computes (score threshold, the three hide-by-default statuses,
    cross-source dedup) - pulled out so the Results tab's nav-bar badge can
    show that same number instead of the raw unfiltered job-store total
    (Zahir's explicit ask 2026-08-06: the badge should reflect what he'll
    actually action, not everything ever scanned). The badge is rendered
    before the tab itself runs, so it calls this with whatever the filter
    widgets were last set to (via their session_state keys, defaulting to
    each widget's own default if the tab's never been visited yet) - the
    page below calls it again with that run's live widget values once
    they've actually rendered. One real consequence: if you change a
    filter while ON the Results tab, the badge can lag one rerun behind the
    page's own heading until the next interaction - a fresh call each rerun
    would need the widgets to render before the nav bar does, which they
    can't. Doesn't compute the intermediate hidden-by-each-filter counts
    the page's own checkbox labels show - those still live inline below,
    since they need the running total at each stage, not just the end
    result."""
    def sort_key(job):
        has_score = "fit_score" in job
        return (has_score, job.get("fit_score", -1), weight_for(job.get("title"), target_roles))

    ranked = sorted(jobs, key=sort_key, reverse=True)
    ranked = [j for j in ranked if "fit_score" in j and j["fit_score"] >= min_score]
    if not show_not_interested:
        ranked = [j for j in ranked if application_status(j) not in NOT_INTERESTED_STATUSES]
    if not show_closed:
        ranked = [j for j in ranked if application_status(j) not in CLOSED_STATUSES]
    if not show_applied:
        ranked = [j for j in ranked if application_status(j) not in APPLIED_STATUSES]
    return dedupe_across_sources(ranked)


# The real Gmail scan (panga-gmail-cta-scan) runs 4x/day - 8am, 12pm, 4pm,
# 8pm local - see docs/email-monitoring-task.md. Flagging staleness at
# double that interval (8 hours) means one slightly-late run never
# false-positives, but a genuinely missed cycle always does.
CTA_SCAN_INTERVAL_HOURS = 4
CTA_SCAN_STALE_AFTER_HOURS = CTA_SCAN_INTERVAL_HOURS * 2


def _format_last_synced(iso_timestamp: str | None) -> tuple[str, bool]:
    """Human-readable "Last synced ..." line for the Call to Action tab's
    status card, plus whether it's stale enough to warrant a warning.
    `iso_timestamp` is fulfillment.get_last_synced_at()'s return value -
    None if fulfillment has never completed a run.

    Returns (text, is_stale). Without this, "All caught up" (positive/
    green) and a last-synced time well past the expected scan cadence look
    equally healthy - nothing distinguishes "genuinely caught up" from
    "caught up as of a stale scan, so something new could be sitting
    unprocessed right now and we just don't know yet" (Zahir's live-testing
    finding, 2026-08-06, production instance - a real "Last synced 19 hours
    ago" read as fine when it very much wasn't)."""
    if not iso_timestamp:
        return "Never synced yet", True
    try:
        synced_at = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return "Last synced: unknown", True
    now = datetime.now(timezone.utc) if synced_at.tzinfo else datetime.now()
    elapsed_seconds = (now - synced_at).total_seconds()
    minutes = max(0, int(elapsed_seconds // 60))
    is_stale = elapsed_seconds / 3600 > CTA_SCAN_STALE_AFTER_HOURS
    if minutes < 1:
        return "Last synced just now", is_stale
    if minutes < 60:
        return f"Last synced {minutes} minute{'s' if minutes != 1 else ''} ago", is_stale
    hours = minutes // 60
    if hours < 24:
        return f"Last synced {hours} hour{'s' if hours != 1 else ''} ago", is_stale
    days = hours // 24
    return f"Last synced {days} day{'s' if days != 1 else ''} ago", is_stale


def _cta_watch_signature() -> tuple:
    """Cheap fingerprint of "would the Call to Action tab look any
    different right now" - covers both things that can change it without
    a click: panga-gmail-cta-scan adding/removing entries (doesn't touch
    last_synced_at, that's fulfillment-only) and panga-cta-fulfillment
    updating draft status on an existing entry or completing a sync.
    Recomputed every tick of _cta_auto_refresh_watcher below; only ever
    compared for equality, never displayed."""
    cta = get_active_cta_emails()
    entries = tuple(sorted(
        (e.get("thread_id"), e.get("draft_created"), e.get("draft_requested"), e.get("draft_link"))
        for e in cta
    ))
    return (entries, get_pending_count(), get_last_synced_at())


@st.fragment(run_every="45s")
def _cta_auto_refresh_watcher() -> None:
    """Replaces the old "Reload view" button (2026-08-08, Mirror's sweep +
    Zahir mockup approval): that button's only real purpose was catching
    background-scheduled-task updates when Zahir hadn't otherwise
    interacted with the page - there's no caching anywhere in this data
    path, so literally any other click already re-reads fresh disk data
    for free. This polls the same on-disk signature on a timer instead and
    forces a full rerun only when it actually changed, so an unattended
    tab quietly catches up on its own. A no-op tick (nothing changed, the
    overwhelmingly common case) touches nothing else on the page - no
    flicker, no risk of interrupting an in-progress "Send and receive"
    click, since that owns its own session_state flag independent of this
    watcher's ticks."""
    sig = _cta_watch_signature()
    watch_key = "cta_watch_signature"
    previous = st.session_state.get(watch_key)
    st.session_state[watch_key] = sig
    if previous is not None and previous != sig:
        st.rerun()


_PROGRESS_CHAR_RE = re.compile(r"([\d,]+) characters")

# Rough expected output length per direct-API call, used only to make a
# progress bar move smoothly with the real character count instead of
# sitting frozen for the entire streamed call - not a promise about final
# length. Every direct-API call in this app reports the same "thinking..."
# / "writing... (N characters so far)" substatus (llm_client.py's
# call_structured, Zahir's explicit ask 2026-07-31: "no spinner anywhere
# should be opaque when the underlying call can report real progress
# instead"), so one lookup table + one fraction function covers all of
# them - drafting, scoring, diagnosis, prep, LinkedIn analysis. Resume
# drafting runs well past everything else here once RESUME_SPEC's real
# output length is accounted for, so it gets its own higher target.
_EXPECTED_RESPONSE_CHARS = {
    "resume": 13_000,
    "cover_letter": 2_500,
    "exec_bio": 2_000,
    "leadership_summary": 2_000,
    "apply_answers": 3_000,
    "job_score": 1_500,
    "prospector_score": 1_500,
    "diagnosis": 1_200,
    "learn": 1_200,
    "prep": 3_000,
    "linkedin_enhance": 3_000,
}
_RESPONSE_CHARS_DEFAULT = 4_000
_PROGRESS_CAP = 0.92  # never shows 100% until the call actually finishes


def progress_fraction(response_key: str, substatus: str | None) -> float:
    """How far through a direct-API call the current substatus represents,
    0-1. "thinking..." (no character count yet) shows a small sliver so the
    bar doesn't sit dead still while Claude reasons before writing anything;
    "writing... (N characters so far)" scales against a rough expected
    length for that call, capped below 100% so the visible jump to "Done"
    always means the response is actually ready, never an estimate that
    happened to land early."""
    if not substatus:
        return 0.0
    match = _PROGRESS_CHAR_RE.search(substatus)
    if not match:
        return 0.05  # "thinking..." or any other pre-writing substatus
    char_count = int(match.group(1).replace(",", ""))
    expected = _EXPECTED_RESPONSE_CHARS.get(response_key, _RESPONSE_CHARS_DEFAULT)
    return min(char_count / expected, _PROGRESS_CAP)


_DRAFT_DONE_GREEN = {"light": "#1a7f37", "dark": "#3fb950"}  # config.toml's own greenColor per mode


def progress_shimmer_css(container_key: str) -> str:
    """A <style> block that reskins the st.progress bar inside
    st.container(key=container_key) with a moving shimmer highlight while
    it's running, then a solid green fill once it hits 100% - the
    "shimmer sweep" look Zahir picked from the style comparison (2026-08-03)
    over three animated alternatives (barber-pole stripes, pulse glow, comet
    trail).

    Targets the real DOM Streamlit renders for st.progress, confirmed by
    direct inspection rather than guessed (inspecting a throwaway probe bar
    at localhost:8504): the visible fill isn't a width-based div, it's a
    full-width div translateX()'d left and clipped by its parent's
    overflow:hidden, so a ::after sweep positioned against the fill's own
    box is naturally masked to only the visible (filled) portion - no need
    to recompute the sweep's bounds as progress changes. The done-state
    color swap keys off the real aria-valuenow="100" the ProgressBar sets,
    so it needs no separate "done" HTML injection - the same static CSS
    handles both states.

    Reads st.context.theme.type (not a prefers-color-scheme media query)
    for the done color, since this app defines both [theme.light] and
    [theme.dark] in config.toml - Zahir can switch modes from Streamlit's
    own settings menu independent of his OS preference, and only the
    server-side theme context reflects that choice correctly."""
    green = _DRAFT_DONE_GREEN.get(st.context.theme.type, _DRAFT_DONE_GREEN["light"])
    key_sel = f".st-key-{container_key}"
    return f"""<style>
{key_sel} [data-testid="stProgressBarTrack"] > div {{
    position: relative;
    overflow: hidden;
}}
{key_sel} [data-testid="stProgressBarTrack"] > div::after {{
    content: "";
    position: absolute;
    top: 0; bottom: 0; left: -40%;
    width: 40%;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.55), transparent);
    animation: panga-progress-shimmer 1.35s ease-in-out infinite;
}}
{key_sel} [role="progressbar"][aria-valuenow="100"] [data-testid="stProgressBarTrack"] > div {{
    background: {green};
}}
{key_sel} [role="progressbar"][aria-valuenow="100"] [data-testid="stProgressBarTrack"] > div::after {{
    content: none;
    animation: none;
}}
@keyframes panga-progress-shimmer {{
    0% {{ left: -40%; }}
    100% {{ left: 110%; }}
}}
@media (prefers-reduced-motion: reduce) {{
    {key_sel} [data-testid="stProgressBarTrack"] > div::after {{
        animation: none;
    }}
}}
</style>"""


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
            if o["channel"] == "email" and o.get("contact_email") and not (o.get("draft_id") or o.get("gmail_draft_id")) and not o.get("draft_requested"):
                if st.button("Request draft", key=f"{key_prefix}_reqdraft_{o['outreach_id']}"):
                    request_draft(o["outreach_id"])
                    st.toast("Flagged - will create a real draft on the next sync (2x/day, or click \"Send and receive\" on Call to Action).", icon=":material/check_circle:")
                    st.rerun()
            elif o.get("draft_requested"):
                st.markdown("Draft requested, not yet created")
        with oc3:
            draft_link = o.get("draft_link") or o.get("gmail_draft_link")
            if draft_link:
                st.link_button("Open draft", draft_link, key=f"{key_prefix}_opendraft_{o['outreach_id']}")
            elif o.get("draft_id") or o.get("gmail_draft_id"):
                st.button("Draft created - check Drafts folder", key=f"{key_prefix}_opendraft_{o['outreach_id']}", disabled=True)
        new_o_tag = st.text_input(
            "Strategy tag (optional)", value=o.get("strategy_tag") or "",
            key=f"{key_prefix}_otag_{o['outreach_id']}", label_visibility="collapsed",
            placeholder="Strategy tag (optional)",
        )
        if new_o_tag != (o.get("strategy_tag") or "") and st.button("Save tag", key=f"{key_prefix}_osavetag_{o['outreach_id']}"):
            set_outreach_strategy_tag(o["outreach_id"], new_o_tag)
            st.rerun()

    # Genuinely no persistence mechanism at all here before this fix (not
    # even a bare key=) - typing into any field or changing the channel
    # dropdown reruns the script and the box visually snapped shut mid-
    # entry (Mirror's proactive sweep, 2026-08-08). Field values themselves
    # survived (separately keyed), but it looked like the form collapsed/
    # lost the in-progress entry. Same key=+on_change="rerun" fix already
    # proven on the Results-tab channel/resume-drafted expanders - explicit
    # expanded=st.session_state.get(...) included too (not just a bare
    # default), confirmed necessary for a PROGRAMMATIC session_state write
    # to actually take effect (key+on_change alone only reliably reflects
    # a real user click on the header, not a Python-side pre-set value -
    # found live while testing this exact fix, 2026-08-08).
    log_outreach_key = f"{key_prefix}_log_outreach_expander"
    with st.expander("Log new outreach", expanded=st.session_state.get(log_outreach_key, False), key=log_outreach_key, on_change="rerun"):
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


def regenerate_resume_and_persist(job: dict, on_progress, success_message: str) -> dict | None:
    """Regenerates just the resume for one job and persists the fresh text/
    score/rationale/next_actions/clarifying_questions/strategy_tag, then
    syncs the workspace .docx - the exact sequence both the clarifying-
    questions regenerate flow and the paste-a-JD regenerate flow end in, so
    it's shared rather than duplicated between them. Shows the toast itself
    on success (success_message gets `.format(score=...)` applied); on
    failure shows the error via st.error and returns None - the caller is
    still responsible for creating/clearing its own progress bar, since
    that UI differs slightly between callers.

    Also runs detect_matched_keyword_regressions() (2026-08-09, real bug
    live-reproduced on the Upstream Bio job - CLAUDE.md known failure
    pattern #2) against the PRE-regenerate resume_text: every regenerate
    is a full rewrite, so a keyword the old draft matched can silently
    fail to survive even when the net ATS score looks flat or improved
    (that job's regenerate fixed "Engineering" while dropping "life
    sciences" - 27/30 both times, invisible unless the two keyword sets
    are actually diffed). Read BEFORE generate_documents() overwrites
    anything, since afterward the old text is gone."""
    old_resume_text = (get_application(job["source"], job["job_id"]) or {}).get("resume_text")
    try:
        regen = generate_documents(job, load_profile(), ["resume"], on_progress=on_progress)
    except (DraftingNotConfigured, DraftingFailed) as exc:
        st.error(str(exc))
        return None
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
    # Same one-shot auto-expand signal the initial "Generate documents"
    # success path sets (Zahir, 2026-08-06) - a regenerate triggered from
    # here (answering a gap question, updating the JD text) must surface
    # its new score/rationale/questions immediately too, not just the
    # first-ever draft.
    st.session_state[f"just_drafted_resume_{job['source']}_{job['job_id']}"] = True
    st.toast(success_message.format(score=new_resume["ats_score"]), icon=":material/check_circle:")
    regressions = detect_matched_keyword_regressions(
        job.get("ats_required_keywords") or [], job.get("ats_preferred_keywords") or [],
        old_resume_text, new_resume["text"],
    )
    if regressions:
        # st.warning() here would flash and vanish - both callers
        # unconditionally st.rerun() right after this function returns, on
        # the same success path that would have rendered it (the exact
        # "flash and vanish" anti-pattern the project's own UI readability
        # rule exists to prevent; the static per-file guard test can't
        # catch this instance since the warning and the rerun are in two
        # different functions). st.toast() survives a rerun - use that.
        st.toast(
            "This rewrite may have traded matches: it no longer covers "
            + ", ".join(f'"{label}"' for label in regressions) +
            " even though the last draft did - full rewrites don't "
            "guarantee an earlier match survives. Worth checking the new text.",
            icon=":material/warning:",
        )
    return new_resume


def _job_has_captured_jd_text(job: dict) -> bool:
    return bool((job.get("qualification_summary") or job.get("description") or "").strip())


def _regenerate_with_progress(job: dict, key_suffix: str, working_message: str, success_message: str) -> None:
    """Shared progress-bar-then-regenerate sequence for both the paste-a-JD
    flow and the view/update-an-existing-JD flow below - both end in the
    exact same "show a shimmering progress bar while
    regenerate_resume_and_persist() runs, then either clear it on failure
    or mark it done and rerun" sequence, so it's factored out rather than
    duplicated a third time."""
    regen_bar_key = f"jd_regen_progress_bar_{key_suffix}"
    with st.container(key=regen_bar_key):
        regen_bar = st.progress(0, text=working_message)
    st.html(progress_shimmer_css(regen_bar_key))

    def _update_regen_progress(i, total, doc_key2, substatus=None):
        label = "Rescoring resume"
        label += f" — {substatus}" if substatus else "..."
        within_doc = progress_fraction(doc_key2, substatus)
        regen_bar.progress(((i - 1) + within_doc) / total, text=label)

    new_resume = regenerate_resume_and_persist(job, _update_regen_progress, success_message)
    if new_resume is None:
        regen_bar.empty()
    else:
        regen_bar.progress(1.0, text=":material/check_circle: Done.")
        st.rerun()


def render_jd_view_or_update_box(job: dict, key_prefix: str) -> str | None:
    """Collapsed-by-default view of a job's currently-stored JD text,
    editable in place - Zahir's ask 2026-08-06: the paste box was write-
    only (paste, save, rescore, and the pasted text was never visible
    again). Shown whenever a job already has real JD text - the opposite
    branch from the no-JD paste prompts, so the two never fight each
    other (a job is always in exactly one state at a time: needs a paste,
    or already has text to view/update).

    Collapsed by default per Zahir's explicit ask, so a job that's fine as-
    is doesn't clutter the row/score-card with an open text box. Keyed on
    a hash of the current text (not just key_prefix alone) - the same
    stale-widget-value pattern CLAUDE.md's HCI standard already calls out
    elsewhere in this file (strategy-tag/clarifying-question boxes):
    Streamlit ignores a new `value=` once a `key` already has session-
    state, so if the stored text ever changes some other way while this
    key stays constant, the box would keep silently showing the old text.

    Returns the new text if the user clicked "Update job description" with
    genuinely changed, non-blank text - None otherwise (including "clicked
    but nothing changed", which shows an info toast instead of pretending
    to save). The caller decides what happens next (persist only vs.
    persist + regenerate a draft), since that differs between the
    proactive (before-drafting) and post-hoc (already-drafted) call sites."""
    current_text = job.get("description") or job.get("qualification_summary") or ""
    text_key = f"jd_view_{key_prefix}_{abs(hash(current_text)) % 10_000_000}"
    with st.expander("View / update job description", expanded=False):
        updated_text = st.text_area("Job description", value=current_text, key=text_key, height=150)
        if st.button("Update job description", key=f"jd_view_save_{key_prefix}"):
            stripped = updated_text.strip()
            if not stripped:
                st.toast("The job description can't be cleared here - paste replacement text instead.", icon=":material/warning:")
            elif stripped == current_text.strip():
                st.toast("No changes to save.", icon=":material/info:")
            else:
                return stripped
    return None


def render_paste_jd_notice() -> None:
    """The shared "why there's no JD text" framing - Zahir's explicit
    product direction 2026-08-06: this must never read as a limitation to
    apologize for. Some job sites intentionally block automated fetching to
    protect themselves and their users from scraping/bots - Panga
    respecting that instead of trying to bypass it is a deliberate,
    security-conscious design choice, not a gap. Shared by both the
    proactive (before any document is drafted) and post-hoc (an already-
    drafted resume's score card) paste prompts below, so the framing can't
    drift between the two."""
    st.info(
        "We don't have this posting's full description yet - some job "
        "sites intentionally block automated access to protect themselves "
        "and their users from scraping, and Panga respects that rather "
        "than trying to bypass it. Paste the job description below and "
        "we'll tailor against it directly.",
        icon=":material/shield:",
    )


def render_paste_jd_prompt_before_drafting(job: dict, app_record: dict) -> None:
    """Proactive paste-JD prompt - Zahir's follow-up ask 2026-08-06: the
    post-hoc version below only ever appeared after a resume had already
    been drafted blind against no real JD text, buried inside that
    drafted resume's own expander. The actual ask is for this to be
    available BEFORE any document gets generated at all, right at the
    job-row level next to the doc-type checkboxes/Generate button, so the
    very first document Panga drafts for this job - resume, cover letter,
    exec bio, leadership summary, or the Apply Assist packet, not just the
    resume - is already tailored against the real JD rather than drafted
    blind and fixed up after the fact.

    For a manually-pasted JD (no extension capture), saving does NOT
    trigger a regenerate the way the post-hoc version does - nothing has
    been drafted yet to regenerate. It just persists the text via
    search.job_store.update_job_description() (clears the stale empty
    ats_required_keywords/ats_preferred_keywords cache too), so whatever
    gets drafted next through the normal "Generate documents" button reads
    it naturally through the same generate_documents() call every draft
    already goes through - no new downstream code path. See the
    extension-capture branch below for why THAT path additionally
    auto-scores inline instead of stopping at "saved."

    This is additive, not a replacement: the post-hoc render_paste_jd_prompt()
    stays in place too, so someone who already has a blind-drafted resume
    can still paste a JD there and get everything correctly redrafted.

    app_record: needed (not looked up here) because the caller already has
    it and the extension-capture branch below passes it straight into
    render_analyze_fit_section() - avoids a second get_application() read
    for the same job in the same render."""
    job_key = f"{job.get('source')}_{job.get('job_id')}"

    # Extension auto-fill (2026-08-08): matches the extension's last capture
    # against THIS job's own posting_url (only LinkedIn/Dice jobs have one
    # from add_manual_job()/the Dice scraper, so this is a no-op for every
    # other source). Only ever seeds the text_area's initial value - actual
    # persistence happens below, either automatically (a real capture
    # exists) or via the explicit Save button (manual paste, no capture).
    # The capture's own timestamp is folded into the widget key (codemap's
    # documented Streamlit gotcha: a bare key ignores a new value= once
    # session_state already has that key) so a fresh capture for the same
    # job actually replaces stale text instead of being ignored.
    #
    # Looked up BEFORE deciding whether to show render_paste_jd_notice()'s
    # "we don't have this posting's full description yet" banner - Zahir
    # caught this live 2026-08-09 (Tarkett CIO, real extension capture): with
    # both always rendered, that banner sat directly above the auto-filled
    # text and the "Auto-filled" badge, contradicting it ("we don't have it"
    # right next to "here it is"). A real capture means we DO have it, so the
    # "we don't have it" framing (and its "sites intentionally block
    # automated access" explanation, which doesn't even apply here) no
    # longer belongs on screen at all.
    capture = extension_bridge.get_capture_for_url(job.get("posting_url"))
    capture_ts = capture["ts"] if capture else "none"
    if capture:
        st.success(
            f"Auto-filled from browser extension - {capture['source'].title() or 'LinkedIn/Dice'}"
            + (f" ({capture['title']} at {capture['company']})" if capture.get("title") or capture.get("company") else ""),
            icon=":material/extension:",
        )
    else:
        render_paste_jd_notice()

    pasted_jd = st.text_area(
        "Paste the job description", key=f"jd_paste_pre_{job_key}_{capture_ts}", height=120,
        value=capture["description"] if capture else "",
        placeholder="Paste the full job posting text here...",
        label_visibility="collapsed",
    )

    if capture:
        # Auto-save + auto-score (2026-08-09, Zahir-approved polish pass -
        # see the hub session's own reasoning, carried into this comment
        # since it explains a real behavior change, not just what the code
        # does): the real human-in-the-loop moment already happened when
        # the user clicked "Send to Panga" on a page they were actually
        # looking at - a second manual "Save" click here is friction
        # nobody actually uses, not real verification. Not a one-time
        # auto-action either: this re-saves and re-scores on every render
        # where the box's CURRENT text differs from what's already
        # persisted, so a later manual edit to this same box goes through
        # the identical auto-save+auto-score path, synchronously, rather
        # than leaving a stale score on screen until some other click
        # happens to trigger a refresh.
        #
        # Cheap enough to not need a stronger change-detection guard than
        # a plain string comparison against job["description"] - matches
        # analyze_fit_before_drafting() itself already being "recomputed
        # fresh at the top of every render" elsewhere in this file, and
        # update_job_description() is a single small locked JSON write,
        # not an AI call (the score is deterministic keyword-overlap
        # arithmetic - ats_score.py's score_resume_against_keywords() -
        # not a fresh AI extraction; a job with no ats_required_keywords
        # cached yet, i.e. never drafted before, scores honestly against
        # "no extractable requirements yet, formatting only" rather than a
        # misleading 100%, so this is safe to show immediately even before
        # any AI call has ever run for this job).
        stripped = pasted_jd.strip()
        if stripped and stripped != (job.get("description") or ""):
            from search.job_store import update_job_description

            update_job_description(job["source"], job["job_id"], stripped)
            job["description"] = stripped
        if stripped:
            st.markdown(":material/check_circle: Saved automatically")
            analysis = _analyze_fit_before_drafting(job, load_profile(), app_record)
            # show_generate_actions=False: this renders directly above the
            # "Documents for this application" checkbox+Generate flow below
            # (which already covers resume among 5 doc types for a job
            # with no draft yet) - showing this section's OWN "Generate
            # resume" button too would be a second, resume-only way to do
            # the same thing one scroll away. See render_analyze_fit_section's
            # own docstring for the full reasoning.
            render_analyze_fit_section(job, app_record, analysis=analysis, show_generate_actions=False)
    else:
        if st.button("Save job description", key=f"jd_paste_pre_save_{job_key}"):
            if not pasted_jd.strip():
                st.toast("Paste the job description text first.", icon=":material/warning:")
            else:
                from search.job_store import update_job_description

                update_job_description(job["source"], job["job_id"], pasted_jd.strip())
                job["description"] = pasted_jd.strip()
                st.toast(
                    "Saved - whatever you generate next will be tailored against it.",
                    icon=":material/check_circle:",
                )
                st.rerun()


def render_paste_jd_prompt(job: dict) -> None:
    """Post-hoc active fallback for a job with no captured JD text
    (ZipRecruiter, Indeed, the industry job boards - confirmed live
    2026-08-06 that these sources genuinely have no real posting text
    available, either because the site blocks automated access or no
    detail-page fetch exists yet). Lives inside an already-drafted resume's
    own score card - kept as a fallback for resumes drafted blind before
    render_paste_jd_prompt_before_drafting() existed, or for anyone who
    skips the proactive prompt and drafts anyway. See that function's
    docstring for the proactive version, which is now the primary path.

    Saving goes through the exact same read path every other job already
    uses - search.job_store.update_job_description() sets job["description"]
    (which tailoring.drafting._extract_ats_keywords() already reads) and
    clears any stale empty ats_required_keywords/ats_preferred_keywords
    cache, so the regenerate below re-extracts for real instead of reusing
    the old "nothing found" result. Unlike the proactive version, this one
    DOES immediately regenerate the resume - a draft already exists here
    and needs to catch up, whereas the proactive version has nothing yet
    to regenerate."""
    render_paste_jd_notice()
    job_key = f"{job.get('source')}_{job.get('job_id')}"
    pasted_jd = st.text_area(
        "Paste the job description", key=f"jd_paste_{job_key}", height=120,
        placeholder="Paste the full job posting text here...",
        label_visibility="collapsed",
    )
    if st.button("Save & rescore", key=f"jd_paste_save_{job_key}"):
        if not pasted_jd.strip():
            st.toast("Paste the job description text first.", icon=":material/warning:")
        else:
            from search.job_store import update_job_description

            update_job_description(job["source"], job["job_id"], pasted_jd.strip())
            job["description"] = pasted_jd.strip()
            _regenerate_with_progress(
                job, job_key, "Rescoring against the pasted description...",
                "Resume rescored against the pasted description - new ATS score {score}/100.",
            )


def _do_regenerate_resume(job: dict) -> None:
    """Shared regenerate-with-progress-bar sequence for both the
    heads-up (proceeds immediately) and the blocking-confirm (proceeds
    after explicit Confirm) paths below - one real drafting call, exactly
    Step 2 of the score-first flow (docs/score-first-resume-flow-spec.md).

    Records the pre-regenerate score under the same prev_ats_score_{...}
    key the "ATS compatibility score" st.metric already reads for its
    delta arrow (Zahir's explicit ask 2026-08-06) - every regenerate goes
    through this one function now (no more per-call-site duplication of
    the old render_gap_questions_section's "Save answers & regenerate"
    button), so it's set here once rather than at each of this function's
    3 call sites."""
    job_key = f"{job.get('source')}_{job.get('job_id')}"
    prev_score = (get_application(job.get("source"), job.get("job_id")) or {}).get("resume_ats_score")
    st.session_state[f"prev_ats_score_{job_key}"] = prev_score
    regen_bar_key = f"analyze_fit_regen_progress_bar_{job_key}"
    with st.container(key=regen_bar_key):
        regen_bar = st.progress(0, text="Generating resume with everything confirmed so far...")
    st.html(progress_shimmer_css(regen_bar_key))

    def _update_regen_progress(i, total, doc_key2, substatus=None):
        label = "Generating resume"
        label += f" — {substatus}" if substatus else "..."
        within_doc = progress_fraction(doc_key2, substatus)
        regen_bar.progress(((i - 1) + within_doc) / total, text=label)

    new_resume = regenerate_resume_and_persist(
        job, _update_regen_progress, "Resume generated - new ATS score {score}/100.",
    )
    if new_resume is None:
        regen_bar.empty()
    else:
        regen_bar.progress(1.0, text=":material/check_circle: Done.")
        st.rerun()


@st.dialog("Regenerate this resume?")
def _confirm_regenerate_dialog(job: dict, cost_info: dict) -> None:
    # Item 6's blocking gate (docs/score-first-resume-flow-spec.md): only
    # reached when there's genuinely nothing new confirmed since the last
    # draft - every regenerate is a full rewrite (see the spec's "Why"),
    # so with no new fact to add there's no expected upside, only the real
    # chance of an accidental rewording dropping a previously-matched
    # keyword. Same confirm/cancel dialog pattern as
    # _confirm_imap_disconnect/_confirm_recovery_code_regen above.
    last_cost = cost_info.get("last_generation_cost")
    cost_line = f" (that draft cost ${last_cost:.2f})" if last_cost is not None else " (real cost not available yet - ATS Engine's cost-logging work, item 7, is still pending)"
    st.markdown(
        f"Nothing new has been confirmed since the last draft{cost_line} - "
        "regenerating now is pure downside risk: every regenerate is a "
        "full rewrite, so there's no new fact to add, only a real chance "
        "of losing a keyword the last draft already matched."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Regenerate anyway", type="primary", key="regen_confirm_confirm"):
            st.session_state.pop("regen_confirm_pending", None)
            _do_regenerate_resume(job)
    with cancel_col:
        if st.button("Cancel", key="regen_confirm_cancel"):
            st.session_state.pop("regen_confirm_pending", None)
            st.rerun()


def render_analyze_fit_section(job: dict, app_record: dict, analysis: dict | None = None, show_generate_actions: bool = True) -> None:
    """Step 1 (analyze fit, no document written) / Step 2 (generate,
    once satisfied) of the score-first resume flow
    (docs/score-first-resume-flow-spec.md, approved 2026-08-08, Option B
    layout - Zahir's pick from 3 mockups). Replaces the old
    render_gap_questions_section "answer a question, regenerate the whole
    resume every single time" pattern - that per-answer regenerate was
    both expensive (a full resume rewrite per answer) and a real
    regression risk (a keyword that matched one draft has no guarantee of
    surviving the next full rewrite, even when the new content is
    objectively better - see the spec's "Why" section for the live
    reproduction that motivated this).

    Scope note: this section is reachable once a resume already exists
    for this job (both call sites already gate on that, matching the old
    function's entry condition) - it does NOT yet apply Step 1 BEFORE the
    very first draft, since that needs ATS Engine's baseline-resume-
    selection work (item 1, not built yet) to have anything real to score
    against. The very first "Documents for this application" generation
    (any doc type, including resume) is unchanged. Once item 1 lands,
    extending Step 1 to cover the pre-first-draft case is a natural next
    step - flagging this now rather than silently deciding scope.

    Shared between the Results tab's per-job panel and the Profile Gaps
    tab, same reasoning as the old function's docstring: one real
    interactive flow, not duplicated.

    Does NOT call _confirm_regenerate_dialog() itself, even though it's
    the thing that decides a confirmation is needed - this function can
    run more than once per script pass (Profile Gaps loops it once per
    job with open questions), and Streamlit allows only one @st.dialog
    call per run. It only ever WRITES st.session_state["regen_confirm_pending"];
    each call site is responsible for checking it and calling the dialog
    exactly once, after this function (and any loop calling it) has
    finished for that run - same pattern _pass_reason_dialog already uses
    for pass_dialog_pending.

    analysis: pass a pre-computed _analyze_fit_before_drafting() result
    when the caller already needed one anyway (Profile Gaps' loop needs
    the real open_questions count for its expander label, computed BEFORE
    the label - see that call site) - avoids running the same real
    scoring/keyword-merge pass twice per job. Computed fresh here when not
    given (the Results-tab call site, which doesn't need it beforehand).

    show_generate_actions: False for the extension auto-save/auto-score
    integration (render_paste_jd_prompt_before_drafting, 2026-08-09) -
    that call site sits directly above the existing "Documents for this
    application" checkbox+Generate flow (which already covers resume among
    5 doc types) for a job with no draft yet, so this section's OWN
    "Generate resume" button would be a second, resume-only way to do the
    same thing one screen-scroll away - the exact "duplicate box" class of
    bug this file has hit and fixed before (see the proactive-prompt/
    post-hoc-prompt duplicate-content fix, 2026-08-06). The score
    card/open-questions display is pure added value there (nothing else
    shows gap questions before a first draft), so only the button row is
    gated, not the whole section."""
    job_key = f"{job.get('source')}_{job.get('job_id')}"
    profile = load_profile()
    if analysis is None:
        analysis = _analyze_fit_before_drafting(job, profile, app_record)
    open_questions = analysis["open_questions"]

    # Item 4: every answer saves immediately, regardless of whether the
    # user ever clicks Generate - not gated behind any button (real gap in
    # the old flow's design intent, explicitly called out in the spec:
    # "easy to accidentally couple 'answer saved' to 'document generated'
    # when restructuring this"). save_answer() (inside save_gap_answers())
    # already upserts-in-place rather than appending a duplicate, so
    # re-saving an unchanged answer on every rerun is safe, not wasteful
    # writes - see profile/interview.py's save_answer() docstring.
    answer_entries = []
    for q in open_questions:
        q_key = f"analyzefit_{job_key}_{abs(hash(q['question'] + '|' + (q.get('suggested_answer') or ''))) % 10_000_000}"
        answer_entries.append({"q": q, "key": q_key})

    header_col, list_col = st.columns([1, 2], gap="medium")
    with header_col:
        with st.container(border=True):
            st.markdown("**Analyze fit**")
            score = analysis["projected_score"]
            st.metric("Projected score", f"{score}/100")
            if analysis.get("projected_rationale"):
                st.markdown(analysis["projected_rationale"])
            st.markdown(f"Scored against: {analysis['baseline_source']}")
            if analysis.get("plateau_note"):
                st.markdown(analysis["plateau_note"])

            if show_generate_actions:
                # analysis is already re-fetched fresh at the top of every
                # render regardless of this click - per the confirmed
                # contract, analyze_fit_before_drafting(job, profile) takes no
                # "give me a new round" flag; calling it again after new
                # answers were just saved naturally excludes those skills via
                # the same profile["gap_interview_answers"] dedup
                # _merge_keyword_gap_questions already uses today. This button
                # exists so there's an explicit, visible action for "I've
                # answered what's here, check for more" - not because the
                # click itself needs to carry any extra state.
                if st.button("Answer more questions", key=f"answermore_{job_key}"):
                    st.rerun()

                regen_needed_confirmation = app_record.get("resume_text") is not None
                if st.button("Generate resume", type="primary", key=f"analyzefit_generate_{job_key}"):
                    if not regen_needed_confirmation:
                        _do_regenerate_resume(job)
                    else:
                        cost_info = _check_regenerate_impact(job, app_record, profile)
                        if cost_info["has_new_info"]:
                            # Non-blocking heads-up (item 6) - proceeds
                            # immediately, doesn't require a second click.
                            fact_count = cost_info.get("new_fact_count")
                            fact_phrase = f"{fact_count} new confirmed fact(s)" if fact_count is not None else "New confirmed facts"
                            est_score = cost_info.get("estimated_new_score")
                            score_phrase = f", should raise your score from {cost_info.get('current_score')} toward ~{est_score}" if est_score is not None else ""
                            cost_estimate = cost_info.get("cost_estimate")
                            cost_phrase = f", estimated cost ${cost_estimate:.2f}" if cost_estimate is not None else ""
                            st.toast(f"{fact_phrase} aren't in your resume yet{score_phrase}{cost_phrase}.", icon=":material/info:")
                            _do_regenerate_resume(job)
                        else:
                            st.session_state["regen_confirm_pending"] = {"job": job, "cost_info": cost_info}
                            st.rerun()

    with list_col:
        if not open_questions:
            if analysis.get("answer_more_exhausted_message"):
                st.markdown(f":material/task_alt: {analysis['answer_more_exhausted_message']}")
            else:
                st.markdown("No open questions right now.")
        else:
            st.markdown(
                "Answer these to raise the score further - only used if "
                "you confirm they're true, nothing is ever invented. Some "
                "boxes are pre-filled with a proposed guess (worded as a "
                "guess, e.g. \"Roughly 8-10 engineers?\") - edit it to "
                "whatever's actually true, or clear it if it's wrong."
            )
        for entry in answer_entries:
            q, q_key = entry["q"], entry["key"]
            is_disqualifier = q["type"] == "disqualifier_check"
            with st.container(border=True):
                badge_col, text_col = st.columns([1, 5])
                with badge_col:
                    if is_disqualifier:
                        st.badge("standing pref", color="gray")
                    elif q.get("point_value") is not None:
                        st.badge(f"+{q['point_value']:g} pts", color="green")
                    else:
                        # Real gap General caught Zahir hit live 2026-08-09:
                        # free-form AI-generated questions (team size,
                        # budget, product names - facts with no
                        # corresponding extracted keyword) have no
                        # deterministic point value to show, and silently
                        # rendering nothing here read as broken rather than
                        # honestly "not computable yet." Says so instead of
                        # guessing a number.
                        st.badge("value not yet known", color="gray")
                with text_col:
                    st.markdown(q["question"])
                if is_disqualifier:
                    st.markdown(
                        ":material/flag: This answer applies to every "
                        "future job match, not just this one - not a "
                        "guess, so the box starts empty."
                    )
                suggested_answer = q.get("suggested_answer") or ""
                answer_value = st.text_area(
                    q["question"], value=suggested_answer,
                    key=q_key, height=68, label_visibility="collapsed",
                    placeholder="Type your answer..." if is_disqualifier else None,
                )
                # Real bug found live-verifying this stub-swap against real
                # data (2026-08-08): a real, hedged suggested_answer guess
                # (e.g. "Unknown - please describe...") is itself non-empty,
                # so checking only "is there text in the box" auto-saved the
                # UNTOUCHED PREFILL as a confirmed real answer on the very
                # first render - before the user ever looked at it, let
                # alone confirmed it. Reproduced live: opening Profile Gaps
                # once silently wrote 31 placeholder "Unknown..." answers
                # into the profile as if they were real facts. This is
                # exactly the CLAUDE.md HCI principle already on record
                # ("keep genuine guesses hedged and editable, never
                # asserted") - a guess sitting in the box isn't consent,
                # only a genuine edit away from that exact prefill is.
                if answer_value and answer_value.strip() and answer_value != suggested_answer:
                    save_gap_answers(job, [{
                        "skill": q["skill"], "type": q["type"],
                        "answer": answer_value, "question": q["question"],
                    }])


def render_answered_gap_questions() -> None:
    """Retrievable/editable history of every confirmed gap answer - Zahir's
    ask 2026-08-06: once a question is genuinely answered, it correctly
    drops out of the "open" list forever (both the AI's own dedup and
    _merge_keyword_gap_questions's profile-history check stop re-asking it)
    - but that meant there was no way to go back and see or edit what was
    actually answered. Same "retrievable, editable" principle already built
    for the JD-paste box, applied here.

    Profile-wide, not per-job (gap_interview_answers lives on the master
    profile - a confirmed fact applies to every future job's scoring, not
    just the one that first surfaced it) - a single top-level section here,
    unlike the per-job "open questions" list above it. Collapsed by default
    (same as the JD view/update box) so a candidate with nothing to review
    isn't shown a wall of settled history.

    Editing calls profile.interview.save_answer() again, which now updates
    the existing entry in place (2026-08-06 fix - it used to silently
    append a duplicate for the same skill instead of reflecting the
    current, latest answer)."""
    profile = load_profile()
    answers = profile.get("gap_interview_answers", [])
    if not answers:
        return

    # Same key=+on_change="rerun" fix as the Results-tab channel/resume-
    # drafted expanders - this one collapsed while editing a past answer
    # (Mirror's proactive sweep, 2026-08-08). Rendered exactly once per
    # script run (profile-wide, not per-job - see docstring above), so a
    # static key is safe, no collision risk. Explicit
    # expanded=st.session_state.get(...) (not just a bare False default) -
    # confirmed necessary for a programmatic session_state write to
    # actually take effect, not just a real click on the header.
    with st.expander(
        f"Previously answered ({len(answers)})",
        expanded=st.session_state.get("previously_answered_expander", False),
        key="previously_answered_expander", on_change="rerun",
    ):
        for entry in answers:
            skill = entry.get("skill", "")
            question_text = entry.get("question") or f"Confirm: {skill}"
            is_disqualifier = entry.get("is_disqualifier", False)
            entry_key = abs(hash(f"{skill}|{entry.get('date_captured', '')}")) % 10_000_000

            if is_disqualifier:
                st.markdown(":material/flag: Applies to every future job match, not just one.")
            st.markdown(f"Confirmed {entry.get('date_captured', 'date unknown')} - {entry.get('role_context', '')}")
            new_answer = st.text_area(
                question_text, value=entry.get("answer", ""),
                key=f"answered_edit_{entry_key}", height=68,
            )
            if st.button("Update answer", key=f"answered_save_{entry_key}"):
                stripped = new_answer.strip()
                if not stripped:
                    st.toast("Can't clear an answer here - edit it to replacement text instead.", icon=":material/warning:")
                elif stripped == (entry.get("answer") or "").strip():
                    st.toast("No changes to save.", icon=":material/info:")
                else:
                    from datetime import datetime, timezone

                    from profile.interview import save_answer

                    # UTC, not local time - must match drafting.py's
                    # save_gap_answers() and applications.py's
                    # documents_drafted_at (real bug found live 2026-08-08:
                    # a local-time date here silently disagreed with the
                    # UTC-stamped draft timestamp for part of every day).
                    save_answer(
                        skill=skill, role_context=entry.get("role_context", ""), answer=stripped,
                        date_captured=datetime.now(timezone.utc).date().isoformat(), question=question_text,
                        is_disqualifier=is_disqualifier,
                    )
                    st.toast("Answer updated.", icon=":material/check_circle:")
                    st.rerun()
            st.divider()


def go_to_prep(target: dict) -> None:
    """Jumps to the Interview Prep tab with enough context to hand off to
    Claude Code - the tab itself doesn't generate anything, it just shows
    what to ask for. target is either {"kind": "job", "source", "job_id",
    "job_label"} (from Results, where the job is known for certain) or
    {"kind": "email", "thread_id", "subject", "sender", "web_link"} (from
    Call to Action, where the email might not be linked to a tracked
    application yet - Claude resolves that ambiguity in conversation, same
    as the existing suggest_status matching does)."""
    st.session_state["active_tab"] = "prep"
    st.session_state["prep_target"] = target
    st.rerun()


# Structured reasons for the inline "Pass" action below (Zahir's explicit
# ask, relayed via hub 2026-08-06): categories, not open-ended prose, since
# the plan is to look for patterns across them later. Deliberately reuses
# applications.py's existing skip_reason field/storage rather than adding a
# parallel one - the category (or free text for "Something else") becomes
# the skip_reason string exactly like the existing full-detail-panel "Why
# not interested?" box already does, so get_unreviewed_skip_reasons() and
# the rest of that review loop keep working unchanged on both paths.
PASS_REASON_CATEGORIES = [
    "Wrong seniority level",
    "Not my industry/domain",
    "Comp too low",
    "Location doesn't work",
    "Something else",
]


@st.dialog("Why pass on this job?")
def _pass_reason_dialog(source: str, job_id: str, label: str) -> None:
    st.markdown(f"**{label}**")
    reason_category = st.radio("Reason", PASS_REASON_CATEGORIES, key="pass_reason_category")
    free_text = ""
    if reason_category == "Something else":
        free_text = st.text_area("What's the reason?", key="pass_reason_free_text")
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Confirm pass", type="primary", key="pass_reason_confirm"):
            # Store BOTH the category and the typed detail, not one or the
            # other (Zahir's original ask, weeks ago - never actually built:
            # caught live-testing again, 2026-08-07). "\n"-joined so
            # skip_reason.split("\n", 1) cleanly separates "which bucket"
            # from "what he actually said" for future data-profiling, while
            # every other category (no free text collected) still stores as
            # a single plain string, unchanged.
            if reason_category == "Something else" and free_text.strip():
                skip_reason = f"{reason_category}\n{free_text.strip()}"
            else:
                skip_reason = reason_category
            upsert_application(source, job_id, status="not interested", skip_reason=skip_reason)
            st.session_state.pop("pass_dialog_pending", None)
            st.toast("Marked not interested.", icon=":material/check_circle:")
            st.rerun()
    with cancel_col:
        if st.button("Cancel", key="pass_reason_cancel"):
            st.session_state.pop("pass_dialog_pending", None)
            st.rerun()


@st.dialog("Disconnect this account?")
def _confirm_imap_disconnect(acct: str) -> None:
    # Used to delete credentials immediately on click, no undo, no
    # confirmation at all (Mirror's fine-needle audit, 2026-08-06).
    st.markdown(
        f"Disconnect **{acct}**? This deletes its stored credentials "
        "immediately - there's no undo. You'd need to reconnect and "
        "re-enter them from scratch to use this account again."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Disconnect", type="primary", key="imap_disconnect_confirm"):
            imap_client.remove_account(acct)
            st.session_state.pop("imap_disconnect_pending", None)
            st.toast(f"Disconnected {acct}.", icon=":material/check_circle:")
            st.rerun()
    with cancel_col:
        if st.button("Cancel", key="imap_disconnect_cancel"):
            st.session_state.pop("imap_disconnect_pending", None)
            st.rerun()


def _generate_and_save_prep_round(target_job: dict, round_label: str, interviewers: list[dict]) -> None:
    """The actual research+draft+save for one round - shared by the direct
    (fresh round) path and _confirm_prep_regen_dialog's Confirm button
    (same content, gated behind a warning when it would overwrite an
    already-"ready" round - see that dialog's docstring for why)."""
    start_round(target_job["source"], target_job["job_id"], round_label, interviewers=interviewers or None)
    with st.container(key="prep_progress_bar"):
        prep_bar = st.progress(0, text="Researching the company and interviewer(s)...")
    st.html(progress_shimmer_css("prep_progress_bar"))

    def _update_prep_progress(substatus):
        prep_bar.progress(progress_fraction("prep", substatus), text=f"Drafting prep content - {substatus}")

    try:
        result = generate_prep(target_job, load_profile(), interviewers, on_progress=_update_prep_progress)
    except (DraftingNotConfigured, DraftingFailed) as exc:
        prep_bar.empty()
        st.error(str(exc))
    else:
        prep_bar.progress(1.0, text=":material/check_circle: Done.")
        save_round(
            target_job["source"], target_job["job_id"], round_label,
            interviewers=result["interviewers"] or interviewers,
            company_snapshot=result["company_snapshot"],
            likely_questions=result["likely_questions"],
            questions_to_ask=result["questions_to_ask"],
            status="ready",
        )
        st.session_state["prep_target"] = None
        st.session_state[f"just_generated_prep_{target_job['source']}_{target_job['job_id']}_{round_label}"] = True
        st.toast("Interview prep ready.", icon=":material/check_circle:")
        st.rerun()


@st.dialog("Regenerate this round's prep?")
def _confirm_prep_regen_dialog(target_job: dict, round_label: str, interviewers: list[dict]) -> None:
    # Real risk flagged by Mirror's proactive sweep (2026-08-08), same
    # structural shape as the resume-regenerate bug that motivated the
    # score-first resume flow redesign: save_round() replaces
    # interviewers/company_snapshot/likely_questions/questions_to_ask
    # wholesale on every call, and each call is a FRESH web-search pass -
    # not guaranteed to find at least as much as the last one. A round
    # whose default "Round 1" label the form starts pre-filled with makes
    # this easy to hit by accident: reopen "Prep for this interview" for
    # a round already researched, don't change the label, click Generate
    # again. outcome/outcome_notes (what Zahir actually wrote himself)
    # are correctly never touched by this - this dialog is the same
    # "don't silently overwrite what's already there" principle applied
    # to the rest, via a warning rather than a merge (there's no
    # meaningful way to "merge" two independent research passes the way
    # gap answers merge into a resume - a fresh regenerate is a real,
    # sometimes-worse-off redo, so the honest fix is asking first, not
    # pretending to reconcile them).
    st.markdown(
        f"**{round_label}** already has research and questions from a "
        "previous pass. Regenerating runs a fresh web search and replaces "
        "all of it - interviewers, company snapshot, and questions - "
        "which may find less than before, since it's a new search each "
        "time, not guaranteed to repeat or improve on the last one."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Regenerate anyway", type="primary", key="prep_regen_confirm"):
            st.session_state.pop("prep_regen_confirm_pending", None)
            _generate_and_save_prep_round(target_job, round_label, interviewers)
    with cancel_col:
        if st.button("Cancel", key="prep_regen_cancel"):
            st.session_state.pop("prep_regen_confirm_pending", None)
            st.rerun()


@st.dialog("Replace your recovery code?")
def _confirm_recovery_code_regen() -> None:
    # Used to invalidate the existing code immediately on click, before
    # the "it stops working" warning above had actually been read as a
    # real consequence about to happen, not just background text (Mirror's
    # fine-needle audit, 2026-08-06).
    st.markdown(
        "Generating a new recovery code immediately replaces the current "
        "one - the old code stops working right away, even if you haven't "
        "saved the new one anywhere yet."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Generate new code", type="primary", key="recovery_code_regen_confirm"):
            st.session_state["new_recovery_code"] = generate_recovery_code()
            st.session_state.pop("recovery_code_regen_pending", None)
            st.rerun()
    with cancel_col:
        if st.button("Cancel", key="recovery_code_regen_cancel"):
            st.session_state.pop("recovery_code_regen_pending", None)
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
# Was len(jobs) - the raw, unfiltered job-store total (1225), not what the
# tab actually shows once its own score-threshold and hide-by-default
# filters run (46) - every other tab badge here is an actionable count
# ("Call to action (7)" = emails needing a reply, "Profile gaps (16)" =
# open questions), so Results should follow suit rather than being the one
# outlier showing "everything ever scanned" (Zahir's explicit ask,
# 2026-08-06). Reads the filter widgets' own session_state keys rather
# than recomputing them here, since they're real widgets defined further
# below - falls back to each widget's own default (matching what a
# first-ever visit to the tab would show) if it hasn't been visited yet
# this session.
results_settings = load_settings()
results_target_roles = results_settings.get("target_roles", [])
results_count = len(compute_results_ranked(
    jobs, results_target_roles,
    min_score=st.session_state.get("results_min_score", 70),
    show_not_interested=st.session_state.get("results_show_not_interested", False),
    show_closed=st.session_state.get("results_show_closed", False),
    show_applied=st.session_state.get("results_show_applied", False),
))
results_total_scanned = len(jobs)
prep_in_progress_count = sum(1 for r in prep_records for round_ in r["rounds"] if round_["status"] == "in_progress")
gaps_count = len(get_applications_with_open_clarifying_questions())

st.session_state.setdefault("active_tab", "cta")

# --- Deep-link from the browser extension's post-capture notification
# ("open Panga to the job I just sent" instead of making Zahir hunt for it,
# extension session's ask 2026-08-08). ?job_url=<url-encoded posting URL>,
# matched via the SAME normalization extension_bridge.py already uses for
# its own capture-matching (strip query/fragment/trailing slash, lowercase)
# - not a fresh regex, so a LinkedIn tracking-param variant or a re-visit
# still matches the job's own stored posting_url exactly like it already
# does for auto-fill. Consumed (query param cleared, deep_link_target
# popped) whether or not a match is actually shown - a job that exists but
# is currently hidden by the Results-tab filters (score threshold, hidden-
# closed/applied) still lands cleanly on Results with nothing force-
# selected, rather than either guessing at relaxing filters or leaving a
# stale target lingering in session_state to surprise a much later,
# unrelated rerun once filters happen to change. Ambiguous matches (more
# than one job shares a normalized posting_url) also no-op rather than
# guess which one Zahir meant.
_job_url_param = st.query_params.get("job_url")
if _job_url_param:
    _normalized_target = extension_bridge._normalize_url(_job_url_param)
    _url_matches = [j for j in jobs if extension_bridge._normalize_url(j.get("posting_url") or "") == _normalized_target] if _normalized_target else []
    if len(_url_matches) == 1:
        st.session_state["active_tab"] = "results"
        st.session_state["deep_link_target"] = (_url_matches[0]["source"], _url_matches[0]["job_id"])
    del st.query_params["job_url"]

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

# --- Browser extension status: always rendered, not just when something's
# wrong - the whole point is Zahir should never have to guess/assume the
# extension is running (Panga/CLAUDE.md's HCI standard). One compact line,
# not a full alert box, since "extension is connected" is the common/quiet
# case and shouldn't cost more vertical space than that. ---
_ext_status = extension_bridge.get_heartbeat_status()
if _ext_status["connected"]:
    st.markdown(f":green[●] Browser extension connected - last check-in {int(_ext_status['seconds_ago'])}s ago")
else:
    st.markdown(":red[●] Browser extension not detected - paste job descriptions manually below")

# --- Tab bar ---
SIGNAL_TYPE_LABELS = {
    "late_stage_trial": "Late-stage clinical trial",
    "commercial_hiring": "Commercial-build hiring",
    "funding_event": "Funding/IPO filing",
    "regulatory_filing": "Regulatory filing (deprecated signal - see note above)",
}
# Known channels the "Add a job manually" fallback can attribute a posting
# to - kept in sync with the sources industry_boards.py/company_sites.py
# actually search, plus LinkedIn (default, since it's the one channel with
# no automated search at all) and "Other" for anything not listed.
MANUAL_JOB_SOURCE_OPTIONS = [
    "linkedin", "Rigzone", "IChemE Job Board", "Planet Pharma", "BioSpace",
    "Beacon Hill Life Sciences", "Atrium", "GForce Life Sciences", "Other",
]
TAB_ICONS = {
    "cta": ":material/notifications_active:",
    "results": ":material/work:",
    "prospector": ":material/travel_explore:",
    "prep": ":material/school:",
    "gaps": ":material/quiz:",
    "linkedin": ":material/badge:",
    "support": ":material/support_agent:",
    "settings": ":material/settings:",
}
TABS = [
    ("cta", f"Call to action ({cta_count})" if cta_count else "Call to action"),
    ("results", f"Results ({results_count})"),
    ("prospector", "Prospector"),
    ("prep", f"Interview prep ({prep_in_progress_count})" if prep_in_progress_count else "Interview prep"),
    ("gaps", f"Profile gaps ({gaps_count})" if gaps_count else "Profile gaps"),
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
        "Upload your resume and other background documents here, plus your "
        "LinkedIn profile export and LinkedIn connections export."
    )

    st.subheader("Resume & other source documents")
    st.markdown(
        "\"Resume\" feeds the gap-probing interview and your target-role "
        "suggestions - it does **not** feed what gets drafted into tailored "
        "documents or how postings are scored against you. Those pull from "
        "your master profile (built from a one-time structuring pass over "
        "your resume, not automatically kept in sync) - re-uploading a "
        "resume here won't change drafted documents until that profile is "
        "updated too. \"Context\" is background material like an executive "
        "bio or leadership summary. \"Reference\" is an example of a "
        "previously-tailored application - used as a style example, not "
        "copied verbatim."
    )
    manifest_entries = load_manifest_result()
    resume_entries = [e for e in manifest_entries if e["category"] == "resume"]
    if len(resume_entries) > 1:
        st.warning(
            f"{len(resume_entries)} documents are tagged \"resume\" "
            f"({', '.join(e['source_file'] for e in resume_entries)}). Their "
            "text is being blended together for the gap interview and "
            "target-role suggestions, which can mix outdated and current "
            "content. Keep only your current resume tagged \"resume\" - "
            "remove the others below, or re-tag them as \"context\"."
        )
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
        toast_msg = f"Saved {entry['source_file']} ({entry['word_count']} words)."
        if doc_category == "resume":
            # Nudge straight to the next real step (Zahir's ask: a saved
            # resume should point at what to do with it, not just confirm
            # the save) - target roles/industries sits right below now,
            # not several unrelated sections down the page.
            toast_msg += " Add your target industries below to generate your target roles."
        st.toast(toast_msg, icon=":material/check_circle:")
        st.rerun()

    st.divider()
    st.header("Target roles and industries")
    st.markdown("These control sort order on the Results tab - higher weight surfaces first. Not a search filter; every source is still searched the same way.")

    settings = load_settings()
    profile_for_settings = load_profile()

    st.subheader("Your target industries/verticals")
    st.markdown(
        "What kind of employer or industry are you aiming for? Free text, "
        "one per line - trades vary too widely for a fixed dropdown, and "
        "even within one trade the target companies can differ a lot by "
        "sub-vertical (e.g. a chemical engineer targeting nuclear plants "
        "needs different targets than one targeting plastics plants)."
    )
    industries_text = st.text_area(
        "One per line",
        value="\n".join(settings.get("industries", [])),
        key="industries_text",
    )

    st.subheader("Your seniority / experience level")
    st.markdown(
        "A short self-description used when generating your target roles "
        "below and when scoring job fit - e.g. \"22 years, Director-to-VP "
        "level\" or \"4 years post-residency, attending physician level\"."
    )
    seniority_text = st.text_input(
        "Seniority / years of experience",
        value=profile_for_settings.get("seniority", ""),
        key="seniority_text",
    )

    has_resume = any(e["category"] == "resume" for e in manifest_entries)
    gen_disabled = not has_resume or not industries_text.strip() or not drafting_is_configured()
    if st.button("Generate my target roles from my resume", icon=":material/auto_awesome:", disabled=gen_disabled):
        industries_list = [line.strip() for line in industries_text.splitlines() if line.strip()]
        with st.spinner("Reading your resume and reasoning about target roles..."):
            try:
                proposal = generate_target_roles(ingested_resume_text(), industries_list, seniority_text.strip())
            except (DraftingNotConfigured, DraftingFailed) as exc:
                st.error(str(exc))
            else:
                new_counter = st.session_state.get("target_roles_gen_counter", 0) + 1
                st.session_state["target_roles_gen_counter"] = new_counter
                st.session_state[f"target_roles_seed_{new_counter}"] = proposal["target_roles"]
                st.toast(
                    f"Proposed {len(proposal['target_roles'])} target role(s) below for "
                    f"\"{proposal['ladder_role']}\" in {proposal['ladder_industry']} - "
                    "review, edit, then Save settings.",
                    icon=":material/auto_awesome:",
                )
                st.rerun()
    if not has_resume:
        st.markdown("Upload a resume above first.")
    elif not industries_text.strip():
        st.markdown("Add at least one target industry/vertical above first.")

    st.subheader("Target roles")
    gen_counter = st.session_state.get("target_roles_gen_counter", 0)
    seed_key = f"target_roles_seed_{gen_counter}"
    if seed_key not in st.session_state:
        st.session_state[seed_key] = settings.get("target_roles", [])
    roles_df = st.data_editor(
        st.session_state[seed_key],
        num_rows="dynamic",
        column_config={
            "name": st.column_config.TextColumn("Role name", required=True),
            "priority_weight": st.column_config.NumberColumn("Priority weight", required=True, min_value=0, max_value=10),
        },
        key=f"roles_editor_{gen_counter}",
    )

    st.subheader("Job-board sources")
    st.markdown(
        "Company career sites Panga searches directly, in addition to "
        "USAJOBS and the built-in industry boards - add or remove "
        "companies here, no code change needed. Covers companies whose "
        "careers site runs on Workday, SmartRecruiters, Greenhouse, or "
        "Lever; find a company's own tenant/ID from its careers URL (see "
        "the field hints below)."
    )
    # Source-level activity hint (2026-08-07) - a source producing zero new
    # jobs for several real runs in a row (Rigzone's own volume drop was
    # the live example that motivated this) probably isn't worth keeping,
    # but that's a call for a person to make, not something auto-removed -
    # this just surfaces the real data instead of needing another manual
    # recon pass to notice.
    stale_sources = [s for s in all_tracked_sources() if is_source_stale(s)]
    if stale_sources:
        st.warning(
            "These sources haven't added a new job in several recent runs "
            "- worth checking whether they're still active, or removing "
            f"them: {', '.join(stale_sources)}."
        )
    with st.expander("Manage companies", expanded=False):
        job_sources_data = load_job_sources()

        st.markdown("**Workday** - tenant/site/wd_number come from the company's own myworkdayjobs.com URL, e.g. \"eisai.wd5.myworkdayjobs.com/eisai\" -> tenant \"eisai\", site \"eisai\", WD # 5.")
        workday_rows = st.data_editor(
            [
                {"company_name": c["company_name"], "tenant": c["tenant"], "site": c["site"], "wd_number": c["wd_number"], "limit": c["limit"]}
                for c in job_sources_data["workday"]
            ],
            num_rows="dynamic",
            column_config={
                "company_name": st.column_config.TextColumn("Company", required=True),
                "tenant": st.column_config.TextColumn("Tenant", required=True),
                "site": st.column_config.TextColumn("Site", required=True),
                "wd_number": st.column_config.NumberColumn("WD #", required=True, min_value=1),
                "limit": st.column_config.NumberColumn("Max results", required=True, min_value=1, max_value=100),
            },
            key="job_sources_workday_editor",
        )

        st.markdown("**SmartRecruiters** - company ID is the identifier in the company's careers.smartrecruiters.com/{id} URL, e.g. \"abbvie\".")
        smartrecruiters_rows = st.data_editor(
            [
                {"company_name": c["company_name"], "company_id": c["company_id"], "limit": c["limit"]}
                for c in job_sources_data["smartrecruiters"]
            ],
            num_rows="dynamic",
            column_config={
                "company_name": st.column_config.TextColumn("Company", required=True),
                "company_id": st.column_config.TextColumn("Company ID", required=True),
                "limit": st.column_config.NumberColumn("Max results", required=True, min_value=1, max_value=100),
            },
            key="job_sources_smartrecruiters_editor",
        )

        st.markdown("**Greenhouse** - board token is the identifier in the company's boards.greenhouse.io/{token} URL, e.g. \"stripe\".")
        greenhouse_rows = st.data_editor(
            [
                {"company_name": c["company_name"], "board_token": c["board_token"], "limit": c["limit"]}
                for c in job_sources_data["greenhouse"]
            ],
            num_rows="dynamic",
            column_config={
                "company_name": st.column_config.TextColumn("Company", required=True),
                "board_token": st.column_config.TextColumn("Board token", required=True),
                "limit": st.column_config.NumberColumn("Max results", required=True, min_value=1, max_value=100),
            },
            key="job_sources_greenhouse_editor",
        )

        st.markdown("**Lever** - company slug is the identifier in the company's jobs.lever.co/{slug} URL, e.g. \"palantir\".")
        lever_rows = st.data_editor(
            [
                {"company_name": c["company_name"], "company_slug": c["company_slug"], "limit": c["limit"]}
                for c in job_sources_data["lever"]
            ],
            num_rows="dynamic",
            column_config={
                "company_name": st.column_config.TextColumn("Company", required=True),
                "company_slug": st.column_config.TextColumn("Company slug", required=True),
                "limit": st.column_config.NumberColumn("Max results", required=True, min_value=1, max_value=100),
            },
            key="job_sources_lever_editor",
        )

        if st.button("Save job-board sources"):
            # Advanced per-company filters (e.g. IQVIA's US-only facet) aren't
            # exposed in this table - carry them over by company_name so
            # editing/re-saving the list above doesn't silently drop them.
            existing_facets = {c["company_name"]: c.get("applied_facets") for c in job_sources_data["workday"]}
            new_workday = []
            for row in workday_rows:
                entry = {
                    "company_name": row["company_name"], "tenant": row["tenant"], "site": row["site"],
                    "wd_number": int(row["wd_number"]), "limit": int(row["limit"]),
                }
                facets = existing_facets.get(row["company_name"])
                if facets:
                    entry["applied_facets"] = facets
                new_workday.append(entry)
            new_smartrecruiters = [
                {"company_name": row["company_name"], "company_id": row["company_id"], "limit": int(row["limit"])}
                for row in smartrecruiters_rows
            ]
            new_greenhouse = [
                {"company_name": row["company_name"], "board_token": row["board_token"], "limit": int(row["limit"])}
                for row in greenhouse_rows
            ]
            new_lever = [
                {"company_name": row["company_name"], "company_slug": row["company_slug"], "limit": int(row["limit"])}
                for row in lever_rows
            ]
            try:
                save_job_sources({
                    "workday": new_workday, "smartrecruiters": new_smartrecruiters,
                    "greenhouse": new_greenhouse, "lever": new_lever,
                })
            except Exception as exc:
                st.error(f"Failed to save job-board sources: {exc}")
            else:
                st.toast("Saved job-board sources.", icon=":material/check_circle:")
                st.rerun()

    st.subheader("Job-alert email senders")
    st.markdown(
        "Senders/domains Panga scans for job-listing digest emails "
        "(LinkedIn, Lensa, etc.), once a day - a listing found from an "
        "address below gets added automatically, the same way manually "
        "pasting one does. Nothing outside this list gets scanned, so add "
        "a sender here (no code change needed) rather than expecting it "
        "to be picked up automatically."
    )
    with st.expander("Manage job-alert senders", expanded=False):
        job_alert_senders_data = load_job_alert_senders()
        job_alert_rows = st.data_editor(
            [{"sender": e["sender"], "source": e.get("source", "")} for e in job_alert_senders_data],
            num_rows="dynamic",
            column_config={
                "sender": st.column_config.TextColumn(
                    "Sender domain or address", required=True,
                    help='e.g. "jobalerts-noreply@linkedin.com" or "lensa.com" - matched against the From header.',
                ),
                "source": st.column_config.TextColumn(
                    "Source tag", required=True,
                    help='What to label listings from this sender with, e.g. "linkedin" or "lensa" - groups with the rest of Panga\'s per-channel data.',
                ),
            },
            key="job_alert_senders_editor",
        )
        if st.button("Save job-alert senders"):
            try:
                save_job_alert_senders([
                    {"sender": row["sender"].strip(), "source": row["source"].strip()}
                    for row in job_alert_rows
                    if row.get("sender", "").strip()
                ])
            except Exception as exc:
                st.error(f"Failed to save job-alert senders: {exc}")
            else:
                st.toast("Saved job-alert senders.", icon=":material/check_circle:")
                st.rerun()

    if bhangi_create_issue is not None:
        # Same "no Bhangi checkout -> feature quietly absent, not an error"
        # rule as the Support tab above - this is a convenience on top of
        # Bhangi, not something worth alarming a friend-testing build about.
        with st.expander("Request a new job-board source", expanded=False):
            st.markdown(
                "Know a job board or company career site that isn't listed "
                "above? Describe it here instead of adding it to your own "
                "code - this files a ticket the same way the Support tab "
                "does, so it's tracked and doesn't get lost."
            )
            request_board_name = st.text_input("Job board or company name", key="request_source_name")
            request_board_url = st.text_input("Careers page / job board URL", key="request_source_url")
            request_board_notes = st.text_area(
                "Anything else worth knowing? (optional)",
                placeholder="e.g. which roles you'd expect to find there, or why it's worth adding",
                key="request_source_notes",
            )
            if st.button("Submit request", disabled=not request_board_name.strip() or not request_board_url.strip()):
                description_parts = [f"URL: {request_board_url.strip()}"]
                if request_board_notes.strip():
                    description_parts.append(f"Notes: {request_board_notes.strip()}")
                description_parts.append("Requested from Settings > Job-board sources > Request a new job-board source.")
                try:
                    build = bhangi_detect_build(PROJECT_ROOT) if bhangi_detect_build is not None else None
                    bhangi_create_issue(
                        BHANGI_PROJECT, f"New job source request: {request_board_name.strip()}",
                        "\n".join(description_parts), build=build,
                    )
                except Exception as exc:
                    st.error(f"Failed to submit request: {exc}")
                else:
                    for key in ("request_source_name", "request_source_url", "request_source_notes"):
                        del st.session_state[key]
                    st.toast("Request submitted - thanks, this has been queued for review.", icon=":material/check_circle:")
                    st.rerun()

    st.subheader("USAJOBS job series")
    st.markdown("See \"Occupations and job series\" on usajobs.gov for codes. This runs alongside keyword search (not instead of it) - government classification is inconsistent, so restricting to one series alone would miss real matches filed under a different code. The compatibility score, not this filter, is what actually screens for relevance.")
    job_series_text = st.text_area(
        "One code per line, e.g. 2210 for Information Technology Management",
        value="\n".join(settings.get("usajobs_job_series", [])),
    )

    st.subheader("Adzuna search countries")
    if adzuna_is_configured():
        st.markdown("Which countries to search via the Adzuna aggregator, in addition to USAJOBS/company sites/industry boards. Adzuna's free tier has a daily call limit shared across every role x country combination below, so keep this to countries you're actually job-hunting in.")
    else:
        st.markdown("Not set up yet - free registration at [developer.adzuna.com](https://developer.adzuna.com/), then add ADZUNA_APP_ID/ADZUNA_APP_KEY to your .env file. The countries picked below take effect once that's done.")
    aggregator_countries = st.multiselect(
        "Countries", sorted(ADZUNA_COUNTRIES), default=settings.get("aggregator_countries", []),
        format_func=lambda c: c.upper(),
    )

    if st.button("Save settings"):
        settings["target_roles"] = roles_df
        settings["industries"] = [line.strip() for line in industries_text.splitlines() if line.strip()]
        settings["usajobs_job_series"] = [line.strip() for line in job_series_text.splitlines() if line.strip()]
        settings["aggregator_countries"] = aggregator_countries
        try:
            save_settings(settings)
            update_profile_field("seniority", seniority_text.strip())
        except Exception as exc:
            st.error(f"Failed to save settings: {exc}")
        else:
            st.success("Saved.")

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
        st.toast("Saved. Go to the LinkedIn tab and click \"Analyze profile\" to get suggestions.", icon=":material/check_circle:")
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
    st.header("Gmail connection")
    st.markdown(
        "Panga reads and labels your inbox, and drafts replies, using "
        "Gmail's own official API - not a browser extension or shared "
        "login. Google requires every app to register its own credentials, "
        "so this needs a one-time setup of your own Google Cloud project "
        "and OAuth client. Takes a few minutes, once - the steps below open "
        "the exact right page each time."
    )

    if gmail_client.is_configured():
        st.success("Gmail credentials found - see \"Test connection\" below to confirm it actually works.")
    else:
        st.info("Not set up yet - follow the steps below in order.")

    with st.container(border=True):
        st.markdown("**Step 1 - Create a Google Cloud project**")
        st.markdown("Name it anything (e.g. \"Panga\"). Skip this if you already have a project you'd rather use.")
        st.link_button("Open: Create a new project", "https://console.cloud.google.com/projectcreate")

    with st.container(border=True):
        st.markdown("**Step 2 - Enable the Gmail API**")
        st.markdown("Make sure your new project is selected (top of the page), then click \"Enable\".")
        st.link_button("Open: Enable Gmail API", "https://console.cloud.google.com/apis/library/gmail.googleapis.com")

    with st.container(border=True):
        st.markdown("**Step 3 - Configure the OAuth consent screen**")
        st.markdown(
            "Choose **External**. Fill in an app name (e.g. \"Panga\") and "
            "your own email as the support and developer contact. On the "
            "\"Test users\" screen, add your own Gmail address - this is "
            "what lets you actually sign in during testing, before any "
            "formal Google review. The first time you connect, you'll see "
            "a \"Google hasn't verified this app\" warning - that's "
            "expected for a personal, unreviewed OAuth client; click "
            "**Continue** (not \"Back to safety\")."
        )
        st.link_button("Open: OAuth consent screen", "https://console.cloud.google.com/apis/credentials/consent")

    with st.container(border=True):
        st.markdown("**Step 4 - Create an OAuth client**")
        st.markdown(
            "Click \"Create Credentials\" > \"OAuth client ID\". Choose "
            "**Desktop app** as the application type (not \"Web "
            "application\" - Desktop is what lets this run without a "
            "public redirect URL). Name it anything, then click the "
            "download icon next to the new client to save its JSON file."
        )
        st.link_button("Open: Create OAuth client", "https://console.cloud.google.com/apis/credentials")

    with st.container(border=True):
        st.markdown("**Step 5 - Add the downloaded file**")
        downloads_hits = gmail_client.find_downloaded_credentials()
        if downloads_hits:
            hit_labels = [f"{p.name} (modified {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M})" for p in downloads_hits]
            chosen_idx = st.selectbox(
                "Found matching file(s) in your Downloads folder:",
                range(len(downloads_hits)), format_func=lambda i: hit_labels[i],
                key="gmail_cred_downloads_pick",
            )
            if st.button("Use this file", type="primary", key="gmail_cred_use_downloaded"):
                gmail_client.install_credentials_from_path(downloads_hits[chosen_idx])
                st.toast("Gmail credentials saved.", icon=":material/check_circle:")
                st.rerun()
            st.markdown("Or upload the file manually below instead.")
        else:
            st.markdown("No matching file spotted yet in your Downloads folder - download it from Step 4 above, then check back here (or upload it manually below).")

        uploaded_gmail_cred = st.file_uploader("Or upload the client-secret JSON manually", type=["json"], key="gmail_cred_upload")
        if st.button("Save uploaded file", type="primary", disabled=not uploaded_gmail_cred, key="gmail_cred_save_uploaded"):
            try:
                gmail_client.install_credentials_from_bytes(uploaded_gmail_cred.getvalue())
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.toast("Gmail credentials saved.", icon=":material/check_circle:")
                st.rerun()

    with st.container(border=True):
        st.markdown("**Step 6 - Test the connection**")
        st.markdown(
            "The first real connection opens a browser window for you to "
            "approve access - one time only, cached after that. Approve it "
            "there, then come back here."
        )
        if st.button("Test Gmail connection", type="primary", disabled=not gmail_client.is_configured()):
            with st.spinner("Connecting to Gmail - a browser window may open, approve access there..."):
                try:
                    labels = gmail_client.list_labels()
                except gmail_client.GmailNotConfigured as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Couldn't connect: {exc}")
                else:
                    st.success(f"Connected - found {len(labels)} Gmail label(s). The Gmail scan/fulfillment scripts are ready to use.")

    st.divider()
    st.header("Other email accounts")
    st.markdown(
        "Not using Gmail? Connect Outlook, Hotmail, Yahoo, or any other "
        "personal email provider below - same idea as Gmail above (Panga "
        "reads/labels/drafts using each provider's own official access, "
        "never sends anything without you)."
    )

    other_provider = st.selectbox("Provider", ["Outlook", "Hotmail", "Yahoo", "Other"], key="other_email_provider")

    if other_provider in ("Outlook", "Hotmail"):
        st.markdown(
            f"{other_provider} and Outlook are the same Microsoft account "
            "system under the hood, so this one setup covers both."
        )
        if microsoft_client.is_configured():
            st.success("Microsoft app registration found - see \"Test connection\" below.")
        else:
            st.info("Not set up yet - follow the steps below in order.")

        with st.container(border=True):
            st.markdown("**Step 1 - Register an app in Azure**")
            st.markdown(
                "Choose **\"Personal Microsoft accounts only\"** for supported "
                "account types. Under \"Redirect URI\", add a platform of "
                "type **\"Mobile and desktop applications\"** with URI "
                "`http://localhost`."
            )
            st.link_button("Open: Azure App registrations", "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade")

        with st.container(border=True):
            st.markdown("**Step 2 - Save the Application (client) ID**")
            ms_client_id = st.text_input("Application (client) ID", key="ms_client_id_input")
            if st.button("Save", type="primary", disabled=not ms_client_id, key="ms_client_id_save"):
                microsoft_client.save_client_id(ms_client_id)
                st.toast("Microsoft app registration saved.", icon=":material/check_circle:")
                st.rerun()

        with st.container(border=True):
            st.markdown("**Step 3 - Test the connection**")
            st.markdown("Opens a browser window to sign in and approve access - one time only, cached after that.")
            if st.button("Test Microsoft connection", type="primary", disabled=not microsoft_client.is_configured(), key="ms_test_connection"):
                with st.spinner("Connecting - a browser window may open, approve access there..."):
                    try:
                        labels = microsoft_client.list_labels()
                    except microsoft_client.MicrosoftNotConfigured as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        st.error(f"Couldn't connect: {exc}")
                    else:
                        st.success(f"Connected - found {len(labels)} categor{'y' if len(labels) == 1 else 'ies'} defined. Ready to use.")

    else:  # Yahoo / Other - generic IMAP, multi-account
        connected = imap_client.list_configured_accounts()
        if connected:
            st.success(f"Already connected: {', '.join(connected)}")
            for acct in connected:
                # Used to delete credentials immediately on click, no undo,
                # no confirmation at all (Mirror's fine-needle audit,
                # 2026-08-06). Same st.dialog confirm-step pattern as
                # Generate recovery code below.
                if st.button(f"Disconnect {acct}", key=f"imap_disconnect_{acct}"):
                    st.session_state["imap_disconnect_pending"] = acct

        pending_disconnect = st.session_state.get("imap_disconnect_pending")
        if pending_disconnect:
            _confirm_imap_disconnect(pending_disconnect)

        st.markdown("**Connect another account**" if connected else "**Connect an account**")
        imap_email = st.text_input(
            "Email address",
            key="imap_new_email",
            placeholder="you@yahoo.com" if other_provider == "Yahoo" else "you@yourprovider.com",
        )

        if imap_email and st.button("Look up mail server settings", key="imap_detect_btn"):
            with st.spinner("Looking up your provider's mail server..."):
                st.session_state["imap_detected_for"] = imap_email
                st.session_state["imap_detected_settings"] = detect_imap_settings(imap_email)

        detect_attempted_for_current = st.session_state.get("imap_detected_for") == imap_email
        detected = st.session_state.get("imap_detected_settings") if detect_attempted_for_current else None

        if imap_email and detect_attempted_for_current and detected is None:
            st.error("That doesn't look like a valid email address.")
        elif detected is not None:
            if detected.source == "guess":
                st.warning(
                    "Couldn't confirm these automatically - double-check them "
                    "with your email provider if the connection test below fails."
                )
            # Keyed by email so switching to a different address shows that
            # address's own detected values instead of a stale locked-in
            # value from whatever was typed first (see CLAUDE.md's widget-
            # key-staleness note).
            host = st.text_input("IMAP server", value=detected.host, key=f"imap_host_{imap_email}")
            port = st.number_input("Port", value=detected.port, key=f"imap_port_{imap_email}")
            app_password = st.text_input(
                "App password",
                type="password",
                key=f"imap_password_{imap_email}",
                help="Most providers require a generated \"app password\" here, not your regular login "
                     "password, once 2-factor authentication is on - check your provider's account "
                     "security settings for \"App passwords\".",
            )
            if st.button("Save & test connection", type="primary", disabled=not app_password, key="imap_save_test"):
                with st.spinner("Connecting..."):
                    imap_client.save_credentials(imap_email, app_password, host, int(port))
                    try:
                        imap_client.search_messages(imap_email, criteria="ALL", max_results=1)
                    except (imap_client.ImapConnectionError, imap_client.ImapNotConfigured) as exc:
                        st.error(f"Saved, but the connection test failed: {exc}")
                    else:
                        # Was st.success() immediately followed by
                        # st.rerun() - the message rendered for a fraction
                        # of a second before the rerun wiped the page, so
                        # it was never actually seen (the exact app-wide
                        # anti-pattern this project's standing rule already
                        # eliminated everywhere else - Mirror's fine-needle
                        # audit caught this one remaining straggler,
                        # 2026-08-06). st.toast() survives a rerun.
                        st.toast(f"Connected - {imap_email} is ready to use.", icon=":material/check_circle:")
                        st.rerun()

    st.divider()
    st.header("Calendar")
    st.markdown(
        "Connect Google Calendar so interview-request replies can offer a "
        "few real, believable open times instead of just asking the "
        "recruiter to propose their own. Read-only, free/busy times only - "
        "Panga never reads what's actually on your calendar, and never "
        "creates or changes events."
    )
    if google_calendar_client.is_configured():
        st.success("Google Calendar access found - see \"Test connection\" below.")
        if google_calendar_client.using_gmail_credentials():
            st.markdown("Using the same Google sign-in already set up for Gmail above.")
    else:
        st.info("Not set up yet - set up Gmail above first (Calendar can reuse that same sign-in), or add a separate Google Cloud OAuth client for Calendar only.")

    if st.button("Test Google Calendar connection", type="primary", disabled=not google_calendar_client.is_configured(), key="calendar_test_connection"):
        with st.spinner("Connecting to Google Calendar - a browser window may open, approve access there..."):
            try:
                from datetime import datetime as _dt, timedelta as _td
                busy = google_calendar_client.get_busy_intervals(_dt.now(), _dt.now() + _td(days=1))
            except google_calendar_client.GoogleCalendarNotConfigured as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Couldn't connect: {exc}")
            else:
                st.success(f"Connected - found {len(busy)} busy block(s) in the next 24 hours. Ready to use.")

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

    recovery_code_exists = has_recovery_code()
    if recovery_code_exists:
        st.markdown("A recovery code already exists for this data. Generating a new one below replaces it - the old code stops working.")
    else:
        st.markdown("No recovery code has been generated yet - your data has no recovery path if this Windows account is lost.")

    # Used to invalidate the old code immediately on click, before the
    # user had actually seen the "it stops working" warning above land as
    # a real consequence, not just background text (Mirror's fine-needle
    # audit, 2026-08-06). Only gated behind a confirm step when there's an
    # existing code to lose - generating the first-ever code isn't
    # destructive, nothing to confirm away.
    if recovery_code_exists:
        if st.button("Generate recovery code"):
            st.session_state["recovery_code_regen_pending"] = True
    else:
        if st.button("Generate recovery code"):
            st.session_state["new_recovery_code"] = generate_recovery_code()

    if st.session_state.get("recovery_code_regen_pending"):
        _confirm_recovery_code_regen()

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

    sync_pending = get_pending_count()
    sync_icon, sync_headline = (":material/task_alt:", "All caught up") if sync_pending == 0 else (
        ":material/sync:", f"{sync_pending} update{'s' if sync_pending != 1 else ''} to send",
    )
    last_synced_text, sync_is_stale = _format_last_synced(get_last_synced_at())
    if sync_is_stale:
        status_line = f"{sync_icon} **{sync_headline}** · :orange[:material/warning: {last_synced_text} - a new scan may be overdue]"
    else:
        status_line = f"{sync_icon} **{sync_headline}** · {last_synced_text}"
    counts = {cat: sum(1 for e in all_cta if e.get("category") == cat) for cat in CATEGORY_ORDER}

    # Single-line header row (Zahir's live-testing feedback, 2026-08-07 -
    # "the send receive and the offer, interview ... rejection should all
    # be in the same line else it looks NASTY"): status text, both buttons,
    # and all 5 category badges used to be 3 separate stacked
    # rows/containers (this status card, a standalone Reload-view row, and
    # a separate stat-strip container below) - merged into one flex row.
    # Same .st-key-{container} scoped-CSS-reflow pattern as
    # progress_shimmer_css, just applied to every child in this one
    # container instead of per-section. flex-wrap keeps it from
    # overflowing horizontally on a narrower window - it wraps to more
    # lines instead, verified down to mobile width.
    #
    # "Reload view" (the old plain st.rerun() button) is gone as of
    # 2026-08-08 (Mirror's sweep + Zahir mockup approval) - its only real
    # job was catching a background-scheduled-task update when Zahir
    # hadn't otherwise interacted with the page, which
    # _cta_auto_refresh_watcher() below now does on its own timer. Every
    # other click already re-reads fresh disk data for free (no caching in
    # this path), so a manual "reload" button was never actually needed
    # for anything a click elsewhere in the app wouldn't already do.
    header_key = "cta_header_row"
    sync_in_progress_key = "cta_sync_in_progress"
    syncing = st.session_state.get(sync_in_progress_key, False)
    st.html(f"""<style>
.st-key-{header_key}[data-testid="stVerticalBlock"] {{
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: wrap !important;
    gap: 0.6rem;
    align-items: center;
    width: 100% !important;
}}
.st-key-{header_key} [data-testid="stElementContainer"] {{
    width: fit-content !important;
    flex: 0 0 auto !important;
}}
.st-key-manual_sync_button [data-testid="stIconMaterial"] {{
    animation: panga-btn-spin 1s linear infinite;
}}
@keyframes panga-btn-spin {{
    from {{ transform: rotate(0deg); }}
    to {{ transform: rotate(360deg); }}
}}
@media (prefers-reduced-motion: reduce) {{
    .st-key-manual_sync_button [data-testid="stIconMaterial"] {{
        animation: none;
    }}
}}
</style>""")
    with st.container(key=header_key):
        st.markdown(status_line)
        # Two-phase button (Zahir's mockup-approved ask, 2026-08-08 - the
        # click needs its OWN visible feedback, not just the st.spinner
        # overlay below): a single script run can't repaint a widget
        # mid-execution, so the disabled/"Syncing..." state has to be its
        # own rerun before the real work starts, not something toggled
        # around the same st.button() call the click came from.
        clicked = st.button(
            "Syncing..." if syncing else "Send and receive",
            type="primary", key="manual_sync_button",
            icon=":material/progress_activity:" if syncing else None,
            disabled=syncing,
        )
        if clicked and not syncing:
            st.session_state[sync_in_progress_key] = True
            st.rerun()
        if syncing:
            with st.spinner("Syncing - archiving, drafting replies, checking sent drafts..."):
                sync_summary = run_full_fulfillment()
            st.session_state[sync_in_progress_key] = False
            parts = []
            if sync_summary["archived"]:
                parts.append(f"{sync_summary['archived']} archived")
            if sync_summary["cta_drafts"]:
                parts.append(f"{sync_summary['cta_drafts']} repl{'ies' if sync_summary['cta_drafts'] != 1 else 'y'} drafted")
            if sync_summary["outreach_drafts"]:
                parts.append(f"{sync_summary['outreach_drafts']} outreach draft{'s' if sync_summary['outreach_drafts'] != 1 else ''} created")
            summary_text = ", ".join(parts) if parts else "nothing was pending"
            if sync_summary["failures"]:
                st.toast(f"Synced with {sync_summary['failures']} failure(s) - {summary_text}. Try again shortly.", icon=":material/warning:")
            else:
                st.toast(f"Synced: {summary_text}.", icon=":material/check_circle:")
            st.rerun()
        for cat in CATEGORY_ORDER:
            st.badge(f"{CATEGORY_LABELS[cat]} **{counts[cat]}**", color=CATEGORY_COLORS[cat])
    _cta_auto_refresh_watcher()

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

            # "web_link" is the current field name; "gmail_link" is read as a
            # fallback for records written before multi-provider support
            # (Outlook/IMAP have no reliable webmail deep link, so this can
            # legitimately be None - see tailoring/cta_emails.py).
            open_link = email.get("web_link") or email.get("gmail_link")
            open_label = {"gmail": "Open in Gmail", "microsoft": "Open in Outlook"}.get(email.get("provider", "gmail"), "Open inbox")

            actions = st.columns(4 if cat == "interview_request" else 3)
            with actions[0]:
                if open_link:
                    st.link_button(open_label, open_link, key=f"open_cta_{email['thread_id']}")
                else:
                    st.button(f"Check {email.get('account', 'your inbox')}", key=f"open_cta_{email['thread_id']}", disabled=True)
            with actions[1]:
                if email.get("draft_created"):
                    if email.get("draft_link"):
                        st.link_button("Open draft", email["draft_link"], key=f"draft_link_{email['thread_id']}")
                    else:
                        st.button("Draft created - check Drafts folder", key=f"draft_link_{email['thread_id']}", disabled=True)
                elif email.get("draft_requested"):
                    st.button("Draft requested...", key=f"draft_pending_{email['thread_id']}", disabled=True)
                else:
                    if st.button("Draft reply", key=f"draft_cta_{email['thread_id']}"):
                        request_draft(email["thread_id"])
                        st.toast("Draft requested - will be created on the next scan run.", icon=":material/check_circle:")
                        st.rerun()
            with actions[2]:
                if st.button("Dismiss", key=f"dismiss_cta_{email['thread_id']}"):
                    request_archive(email["thread_id"])
                    st.rerun()
            if cat == "interview_request":
                with actions[3]:
                    # Mirror's first UI/UX review pass (2026-08-06) flagged
                    # these 4 buttons as same-weight with no hierarchy - not
                    # fixed by consolidating them (checked against CLAUDE.md's
                    # "is this really one decision?" test: Open in Gmail,
                    # Draft reply, Dismiss, and Prep for this interview are 4
                    # genuinely independent actions a candidate takes at
                    # different times for different reasons, not steps of one
                    # decision). What was missing is visual weight - this is
                    # the one action specific to interview_request and the
                    # highest-value next step, so it gets type="primary" like
                    # "Send and receive" above does for the same reason (the
                    # one action in its row worth drawing the eye to); the
                    # other 3 stay default/secondary.
                    if st.button("Prep for this interview", key=f"prep_cta_{email['thread_id']}", type="primary"):
                        go_to_prep({
                            "kind": "email",
                            "thread_id": email["thread_id"],
                            "subject": email.get("subject"),
                            "sender": email.get("sender"),
                            "web_link": open_link,
                        })
            st.divider()

elif active_tab == "results":
    render_feedback_widget("results")

    settings = load_settings()

    col1, col_manual, col2 = st.columns([1.3, 1.4, 2.3])
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
                    with st.container(key="search_progress_bar"):
                        search_bar = st.progress(0, text=f"Searching 1 of {len(steps)}: {steps[0][1]}...")
                    st.html(progress_shimmer_css("search_progress_bar"))
                    for i, (kind, value) in enumerate(steps, start=1):
                        search_bar.progress((i - 1) / len(steps), text=f"Searching {i} of {len(steps)}: {value}...")
                        if kind == "role":
                            results = search_jobs(keyword=value, results_per_page=50)
                        else:
                            results = search_jobs(job_category_code=value, results_per_page=100)
                        total_new += save_jobs(results)
                    search_bar.progress(1.0, text=":material/check_circle: Done.")
                st.success(f"Found {total_new} new job(s).")
            except USAJobsNotConfigured as e:
                st.error(str(e))
    with col_manual:
        # Zahir couldn't find "Add a job manually" - it was a plain
        # collapsed st.expander sitting below this row, easy to miss as
        # inert prose (Mirror confirmed live on production, 2026-08-07).
        # Approved direction (Zahir picked from 3 mockups): a dedicated,
        # equal-weight button right next to "Run now (USAJOBS)" - same
        # type="primary" as that button, since both are top-level ways to
        # get a job into the Results tab, not a secondary/buried action.
        # Clicking it force-opens the expander below once (see the
        # one-shot pattern comment just above that expander for why).
        if st.button("Add a job manually", icon=":material/add:", type="primary"):
            st.session_state["manual_job_reveal_pending"] = True
            st.rerun()
    with col2:
        st.markdown("This button only covers USAJOBS.gov directly. ZipRecruiter, Dice, and Indeed are searched automatically once a day by the scheduled task instead (they're MCP connector tools, not reachable from this button).")

    # Same one-shot force-expand pattern already proven working elsewhere
    # in this file (Interview Prep's just_generated_prep_* flag): pop the
    # pending flag right before the widget so it force-expands exactly
    # once. Tried a key="manual_job_expander" + expanded=session_state.get(...)
    # version first (which AppTest confirmed as logically correct) but it
    # did not actually open the expander when live-verified in the real
    # browser - a real discrepancy between AppTest's simulated widget
    # reconciliation and the live frontend for this specific key+expanded
    # combination, not just a wait-time/click-delivery issue (confirmed via
    # the native <details>.open DOM property after several isolated
    # retries). This pattern needs no key at all - the expander's own
    # header click still toggles it normally afterward since Streamlit's
    # frontend preserves that toggle client-side across unrelated reruns
    # even for un-keyed expanders (same as Interview Prep's).
    manual_job_reveal = st.session_state.pop("manual_job_reveal_pending", False)
    with st.expander(
        "Add a job manually (e.g. from LinkedIn or a site Panga can't search automatically)",
        expanded=manual_job_reveal,
    ):
        st.markdown(
            "For channels with no public search API and no working scraper "
            "(LinkedIn, or a job board that turned out blocked/JS-rendered), "
            "this is the way to get a posting into Panga - paste it yourself "
            "instead of waiting on an automated search. The description is "
            "saved now rather than fetched live later, since these URLs "
            "often can't be reliably refetched (login wall/bot-check)."
        )
        manual_job_fields = ["manual_job_title", "manual_job_org", "manual_job_location", "manual_job_url", "manual_job_description", "manual_job_source_other"]
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
        manual_source_choice = st.selectbox(
            "Channel this came from", MANUAL_JOB_SOURCE_OPTIONS, key="manual_job_source",
            help="Pick the site the posting is from, so it groups with the rest of Panga's per-channel data instead of showing up unlabeled.",
        )
        manual_source_other = (
            st.text_input("Channel name", key="manual_job_source_other")
            if manual_source_choice == "Other" else None
        )
        manual_description = st.text_area(
            "Paste the job description text", key="manual_job_description", height=200,
        )
        manual_source = (manual_source_other or "Other") if manual_source_choice == "Other" else manual_source_choice
        if st.button("Save job", type="primary", disabled=not (manual_title and manual_org and manual_url)):
            from search.job_store import add_manual_job, update_job_score
            job = add_manual_job(
                title=manual_title, organization=manual_org, location=manual_location,
                description=manual_description, posting_url=manual_url, source=manual_source,
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
                with st.container(key="job_score_progress_bar"):
                    score_bar = st.progress(0, text="Scoring compatibility...")
                st.html(progress_shimmer_css("job_score_progress_bar"))

                def _update_score_progress(substatus):
                    score_bar.progress(progress_fraction("job_score", substatus), text=f"Scoring compatibility - {substatus}")

                try:
                    scored = score_job(job, load_profile(), on_progress=_update_score_progress)
                except (DraftingNotConfigured, DraftingFailed) as exc:
                    score_bar.empty()
                    st.toast(f"Saved, but scoring failed: {exc}", icon=":material/warning:")
                else:
                    score_bar.progress(1.0, text=":material/check_circle: Done.")
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
    # Explicit, stable keys (rather than the auto-generated ones these had
    # before) so compute_results_ranked() can read each filter's current
    # value from st.session_state for the nav-bar badge above, before this
    # widget has rendered on a given run.
    min_score = st.slider(
        "Minimum compatibility score",
        0, 100, 70,
        help="Hides low-fit results (e.g. unrelated roles pulled in by broad keyword matches).",
        key="results_min_score",
    )
    # Unscored jobs are hidden by default, NOT always shown - showing
    # unscored jobs regardless of the slider defeated the purpose of scoring
    # (e.g. a fresh "Run now" search returning an unscored Physician role
    # would display no matter how high the threshold was set). Scoring only
    # happens via the daily scheduled task or a manual Claude pass, so new
    # jobs from "Run now" won't show here until one of those has run.
    ranked = [j for j in ranked if "fit_score" in j and j["fit_score"] >= min_score]

    not_interested_count = sum(1 for j in ranked if application_status(j) in NOT_INTERESTED_STATUSES)
    show_not_interested = st.checkbox(
        f"Show {not_interested_count} job(s) marked 'not interested' (hidden by default, nothing is deleted)",
        key="results_show_not_interested",
    )
    if not show_not_interested:
        ranked = [j for j in ranked if application_status(j) not in NOT_INTERESTED_STATUSES]

    # Separate from "not interested" (Zahir's own preference) - this is the
    # posting itself having closed on the source site (LinkedIn showing "No
    # longer accepting applications"), which he's now hit twice (2026-08-04)
    # scrolling stale results. Same hide-but-never-delete pattern as above -
    # a distinct status/count rather than folding into NOT_INTERESTED so the
    # two different reasons ("I don't want it" vs. "it's gone") stay visible
    # as different information, not conflated into one bucket.
    closed_count = sum(1 for j in ranked if application_status(j) in CLOSED_STATUSES)
    show_closed = st.checkbox(
        f"Show {closed_count} job(s) marked 'closed by employer' (hidden by default, nothing is deleted)",
        key="results_show_closed",
    )
    if not show_closed:
        ranked = [j for j in ranked if application_status(j) not in CLOSED_STATUSES]

    # Same hide-but-never-delete pattern again (2026-08-05 request): once a
    # job is marked "applied" there's nothing left to do on it here, so by
    # default Results should only show jobs still needing a decision -
    # already-applied jobs stay tracked (CTA/status still update normally)
    # but no longer crowd out the ones that actually need attention.
    applied_count = sum(1 for j in ranked if application_status(j) in APPLIED_STATUSES)
    show_applied = st.checkbox(
        f"Show {applied_count} job(s) marked 'applied' (hidden by default, nothing is deleted)",
        key="results_show_applied",
    )
    if not show_applied:
        ranked = [j for j in ranked if application_status(j) not in APPLIED_STATUSES]

    # Cross-source dedup (2026-07-30): if a company has a direct-site channel
    # (Eisai/AbbVie/IQVIA via company_sites.py) and the same real opening also
    # shows up on a job board (Indeed/ZipRecruiter/Dice/USAJOBS/industry
    # boards), the direct-site posting is authoritative - show only that one.
    # See ranking.prioritize.dedupe_across_sources() for the matching rule.
    ranked = dedupe_across_sources(ranked)

    if unscored_count:
        st.markdown(f"{unscored_count} job(s) found but not yet compatibility-scored - hidden until the next scoring pass (daily scheduled task, or ask Claude to score them now).")

    st.subheader(f"{len(ranked)} job(s)")
    # Zahir's explicit ask alongside the badge fix above: don't lose the
    # 1225 total scanned entirely just because the badge/heading now leads
    # with the actionable 46 - keep that transparency, right where both
    # numbers are simultaneously known and can't drift out of sync with
    # each other (this line always reflects the real, live ranked list,
    # unlike the badge above the tabs, which can lag one rerun behind while
    # actively changing a filter - see compute_results_ranked()'s
    # docstring). st.markdown, not st.caption - this project's standing
    # readability rule (Mirror's fine-needle audit caught this exact call
    # as one of the still-remaining violations, 2026-08-06).
    st.markdown(f"{len(jobs)} job(s) scanned, {len(ranked)} match your profile and current filters.")

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
    # Computed once here (not per-job below) - CLAUDE.md's "avoid O(n^2)
    # scans" guidance - so the resume-drafted expander below can
    # default-open for a job with genuinely outstanding questions without
    # re-scanning every application per job row.
    open_gap_question_keys = {
        (a.get("source"), a.get("job_id")) for a in get_applications_with_open_clarifying_questions()
    }

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

        # Deep-link target (see the top-of-script query-param handling) -
        # force-open this channel's expander and select/scroll to the row
        # the moment it's found in THIS channel's own post-filter/dedup
        # list, before the expander below reads its expanded= state. One-
        # shot: popped the instant it's applied so a later, unrelated
        # rerun doesn't keep re-forcing this same job open.
        _deep_link_target = st.session_state.get("deep_link_target")
        if _deep_link_target and _deep_link_target[0] == channel:
            for _idx, _job in enumerate(deduped):
                if (_job.get("source"), _job.get("job_id")) == _deep_link_target:
                    st.session_state[f"channel_expander_{channel}"] = True
                    st.session_state[f"selected_idx_{channel}"] = _idx
                    st.session_state[f"scroll_pending_{channel}"] = True
                    st.session_state.pop("deep_link_target", None)
                    break

        # Investigated a reported inconsistency (2026-08-06, relayed via
        # hub): USAJOBS seen expanded on page load while Indeed was
        # correctly collapsed. Could not reproduce with a genuinely fresh
        # session - a brand-new browser tab against a brand-new server
        # process shows every channel (USAJOBS included) starting
        # collapsed, matching the flat expanded=False every channel gets
        # here (no per-channel branching exists in this loop - all sources,
        # job boards and direct-company-sites alike, go through this same
        # code).
        #
        # A REAL bug in the same neighborhood surfaced later (2026-08-07,
        # Zahir live-testing: answering a clarifying-questions box inside
        # this expander collapsed the whole section on him mid-form). Root
        # cause confirmed by reading the actual installed st.expander's
        # docstring rather than guessing: "When on_change is set to 'rerun'
        # or a callable, setting a key lets you read or update the expanded
        # state via st.session_state[key]" - i.e. key= ALONE (without
        # on_change) does nothing for server-side persistence; Python's
        # `expanded=` argument is authoritative on every single rerun,
        # silently overwriting whatever the user's own click set client-side
        # the moment any nearby content shifts enough to force a resync
        # (which answering a question inside it does). on_change="rerun"
        # below is what actually makes the existing key= functional.
        #
        # expanded= reads session_state explicitly (not just a bare False
        # default) - a real click on the header is correctly reflected by
        # key=+on_change="rerun" alone, but a PROGRAMMATIC session_state
        # write (as opposed to a real click) needs this explicit read to
        # actually take effect - found 2026-08-08 while building the
        # outreach/previously-answered expander fixes and applied back
        # here for the same robustness/testability.
        channel_expander_key = f"channel_expander_{channel}"
        with st.expander(
            f"{channel} ({len(deduped)}{dup_note})",
            expanded=st.session_state.get(channel_expander_key, False),
            key=channel_expander_key, on_change="rerun",
        ):
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
                    "JD": "✓" if _job_has_captured_jd_text(job) else "–",
                    "Posting": job.get("posting_url"),
                    "Pass": "Pass",  # ButtonColumn's label comes from the cell value itself - same word every row on purpose
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

            # Inline "Pass" (Zahir's explicit ask, relayed via hub
            # 2026-08-06): marking a job "not interested" used to mean
            # activating the row, scrolling to its full detail panel, and
            # finding "Mark status" among three side-by-side fields - too
            # many steps for what should be a quick, one-click pass. This
            # button is reachable straight from the table, same ButtonColumn
            # mechanism as the existing Role click-to-activate above. The
            # callback only records WHICH job was clicked (same
            # default-arg-closure pattern as _activate_row, needed because
            # `deduped` is rebound each loop iteration) - the actual dialog
            # is opened once, after the channel loop ends, never from inside
            # a callback or from inside this loop, since Streamlit only
            # allows one dialog function call per script run.
            pass_click_key = f"passclick_{channel}"

            def _trigger_pass(pass_click_key=pass_click_key, channel_deduped=deduped):
                click = st.session_state.get(pass_click_key)
                if click:
                    clicked_job = channel_deduped[click["row"]]
                    st.session_state["pass_dialog_pending"] = {
                        "source": clicked_job.get("source"),
                        "job_id": clicked_job.get("job_id"),
                        "label": job_label(clicked_job),
                    }

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
                    "JD": st.column_config.TextColumn(
                        "JD", alignment="left", width="small",
                        help="Whether Panga has this posting's job description text to score and tailor against.",
                    ),
                    "Pass": st.column_config.ButtonColumn(
                        "Pass", type="tertiary", alignment="left", width="small",
                        help="Mark not interested, with a structured reason for later pattern analysis - doesn't affect what's shown to you.",
                        on_click=_trigger_pass, key=pass_click_key,
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

                # Only shown before a resume exists for this job (same
                # "resume_ats_score is not None" condition the post-hoc
                # score-card box below uses to appear) - Zahir hit this
                # live 2026-08-06: once a resume's already drafted, this
                # proactive box and the post-hoc one showed the exact same
                # content twice on the same screen. The proactive box's
                # whole purpose (prompt/view before anything's drafted) is
                # moot once a draft exists - the post-hoc one takes over
                # from there, same as it already did before this box existed.
                if app_record.get("resume_ats_score") is None:
                    # A live extension capture routes here EVEN when the job
                    # already has saved JD text (real gap found live
                    # 2026-08-09 building this: the very first auto-save
                    # flips _job_has_captured_jd_text() to True, which would
                    # otherwise fall through to the plain view/update-box
                    # branch below on the VERY NEXT rerun - that box has no
                    # auto-score integration and requires an explicit
                    # "Update" click, directly contradicting "not a one-time
                    # auto-action that then locks into a static gate."
                    # Falls back to the plain view/update box once the
                    # capture expires (extension_bridge's own 30-min TTL) or
                    # stops matching - graceful degradation, not a dead end.
                    if extension_bridge.get_capture_for_url(job.get("posting_url")) or not _job_has_captured_jd_text(job):
                        render_paste_jd_prompt_before_drafting(job, app_record)
                    else:
                        pre_job_key = f"pre_{job.get('source')}_{job.get('job_id')}"
                        updated_text = render_jd_view_or_update_box(job, pre_job_key)
                        if updated_text is not None:
                            from search.job_store import update_job_description

                            update_job_description(job["source"], job["job_id"], updated_text)
                            job["description"] = updated_text
                            st.toast(
                                "Updated - whatever you generate next will be tailored against it.",
                                icon=":material/check_circle:",
                            )
                            st.rerun()

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
                        with st.container(key="draft_progress_bar"):
                            progress_bar = st.progress(0, text=f"Drafting 1 of {len(selected)}: {doc_labels[selected[0]]}...")
                        st.html(progress_shimmer_css("draft_progress_bar"))

                        def _update_progress(i, total, doc_key, substatus=None):
                            label = f"Drafting {i} of {total}: {doc_labels[doc_key]}"
                            label += f" — {substatus}" if substatus else "..."
                            within_doc = progress_fraction(doc_key, substatus)
                            progress_bar.progress(((i - 1) + within_doc) / total, text=label)

                        try:
                            drafted = generate_documents(job, load_profile(), selected, on_progress=_update_progress)
                        except (DraftingNotConfigured, DraftingFailed) as exc:
                            progress_bar.empty()
                            st.error(str(exc))
                        else:
                            progress_bar.progress(1.0, text=":material/check_circle: Done.")
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
                            if "resume" in selected and resume_is_scored:
                                # Zahir's explicit ask 2026-08-06: the score,
                                # "why this score" breakdown, and any outstanding
                                # gap questions must be part of what he sees the
                                # moment he generates - not something he has to
                                # notice or go click open afterward. One-shot -
                                # popped the next time this expander renders, so
                                # it doesn't stay force-expanded forever.
                                st.session_state[f"just_drafted_resume_{job['source']}_{job['job_id']}"] = True
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
                        resume_expander_key = None
                        if doc_key == "resume":
                            # Zahir's explicit ask 2026-08-06: right after
                            # generating (or regenerating - answering a gap
                            # question, updating the JD text), the score,
                            # "why this score" breakdown, and any outstanding
                            # questions must be part of what he sees
                            # immediately - not behind a click he has to
                            # remember to make.
                            #
                            # Used to be a one-shot session_state.pop() flag
                            # feeding a bare `expanded=` - correct for
                            # "flash open right after generating", wrong for
                            # "stay open while actively answering the
                            # questions inside it": every rerun answering a
                            # question causes (any text_area losing focus
                            # reruns the script, same as clicking anywhere
                            # else) re-evaluated `expanded=auto_expand` as
                            # False, since the flag was already consumed on
                            # the very first render - collapsing the whole
                            # section out from under him mid-form (2026-08-07
                            # live-testing report). Fixed the same way as the
                            # channel expander above: a real key= +
                            # on_change="rerun" makes st.session_state[key]
                            # the actual source of truth Streamlit itself
                            # maintains across reruns, not something this
                            # code has to re-derive from scratch every time.
                            #
                            # The one-shot force-open on fresh generation
                            # still works - explicitly written into
                            # session_state here, which is the documented,
                            # now-supported way to control a key+on_change
                            # expander from Python. Also defaults open (only
                            # the very first time this key is ever seen in a
                            # session - the `not in st.session_state` guard
                            # keeps this from re-forcing a later manual
                            # collapse back open) if this job genuinely has
                            # unanswered questions, so coming back to a job
                            # left mid-answer doesn't require remembering to
                            # reopen it by hand.
                            resume_expander_key = f"resume_drafted_open_{job.get('source')}_{job.get('job_id')}"
                            just_drafted = st.session_state.pop(f"just_drafted_resume_{job.get('source')}_{job.get('job_id')}", False)
                            has_open_questions = (job.get("source"), job.get("job_id")) in open_gap_question_keys
                            if just_drafted or (resume_expander_key not in st.session_state and has_open_questions):
                                st.session_state[resume_expander_key] = True
                        with st.expander(
                            f"{doc_label} (drafted)",
                            expanded=st.session_state.get(resume_expander_key, False) if resume_expander_key else False,
                            key=resume_expander_key,
                            on_change="rerun" if resume_expander_key else "ignore",
                        ):
                            if doc_key == "resume" and app_record.get("resume_ats_score") is not None:
                                with st.container(border=True):
                                    if _job_has_captured_jd_text(job):
                                        job_key = f"{job.get('source')}_{job.get('job_id')}"
                                        updated_text = render_jd_view_or_update_box(job, job_key)
                                        if updated_text is not None:
                                            from search.job_store import update_job_description

                                            update_job_description(job["source"], job["job_id"], updated_text)
                                            job["description"] = updated_text
                                            _regenerate_with_progress(
                                                job, f"update_{job_key}", "Rescoring against the updated description...",
                                                "Resume rescored against the updated description - new ATS score {score}/100.",
                                            )
                                    else:
                                        render_paste_jd_prompt(job)
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
                                        st.markdown(
                                            "**How to raise it (as of the last Generate):**"
                                        )
                                        for action in next_actions:
                                            st.markdown(f"- {action}")
                                    # Inline here, not just a pointer to the Profile Gaps tab
                                    # (Zahir's correction 2026-08-06, live-testing: expanding a
                                    # specific job's score card is a clear signal of focus on
                                    # THIS job - he wants to answer right there, not context-
                                    # switch to a separate tab). Profile Gaps still exists for
                                    # scanning across every job at once; this is additive, not a
                                    # replacement - same shared render_analyze_fit_section(),
                                    # just rendered in both places now.
                                    render_analyze_fit_section(job, app_record)
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
                        "Edit the .docx files directly in Word if you want to change anything "
                        "(each file is named Name_DocType_Role_Company.docx, same convention as "
                        "before), then click \"Check my edited documents\" below - you'll need to "
                        "do this before marking the job \"applied\"."
                    )
                    folder_row = st.container(horizontal=True)
                    with folder_row:
                        if st.button("Open folder", key=f"openfolder_{job_key}", icon=":material/folder_open:"):
                            if workspace_folder.is_dir():
                                # Local, single-user desktop app (Streamlit and the browser
                                # both run on Zahir's own machine) - os.startfile() just does
                                # what double-clicking the folder in Explorer would do. No
                                # shell involved and workspace_folder is a computed Path from
                                # job data, never a user-typed string, so there's nothing here
                                # for shell-injection-style concerns to attach to.
                                os.startfile(workspace_folder)  # noqa: S606 - Windows-only app, see comment above
                            else:
                                st.toast("Folder not created yet - generate a document first.", icon=":material/warning:")
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
                        ["-", "applied", "interview scheduled", "offer", "rejected", "not interested", "save for later", "closed by employer"],
                        key=f"status_{job.get('source')}_{job.get('job_id')}",
                        help="\"closed by employer\" is for a posting that's stopped accepting applications on the source site (e.g. LinkedIn showing \"No longer accepting applications\") - separate from \"not interested\", which is your own choice.",
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

    # Opened once, after every channel's table has had a chance to set it
    # (Streamlit allows only one dialog function call per script run) -
    # never call _pass_reason_dialog from inside the per-channel loop or
    # from a callback.
    pending_pass = st.session_state.get("pass_dialog_pending")
    if pending_pass:
        _pass_reason_dialog(pending_pass["source"], pending_pass["job_id"], pending_pass["label"])

    # Checked once here, after every job's render_analyze_fit_section()
    # call has already run for this pass - see that function's own
    # docstring for why the dialog can't be called from inside it directly.
    pending_regen_confirm = st.session_state.get("regen_confirm_pending")
    if pending_regen_confirm:
        _confirm_regenerate_dialog(pending_regen_confirm["job"], pending_regen_confirm["cost_info"])

    # A deep-link target that's still set here never matched any channel's
    # post-filter/dedup list this pass - the job exists (it was resolved
    # from a real posting_url match up top) but is currently hidden by the
    # score threshold or a hidden-closed/applied filter. Cleared rather
    # than left to linger in session_state, which would otherwise force-
    # select it unexpectedly on some later, unrelated rerun once a filter
    # happens to change - lands cleanly on Results with nothing selected,
    # same as the "ambiguous match" no-op above.
    st.session_state.pop("deep_link_target", None)

elif active_tab == "prospector":
    render_feedback_widget("prospector")

    st.header("Prospector")
    st.markdown(
        "Watch companies before they post a role, log outreach, track your "
        "coverage and results, and see what's actually working."
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
        with st.container(key="prospector_score_progress_bar"):
            score_compute_bar = st.progress(0, text="Reasoning over your real data...")
        st.html(progress_shimmer_css("prospector_score_progress_bar"))

        def _update_score_compute_progress(substatus):
            score_compute_bar.progress(progress_fraction("prospector_score", substatus), text=f"Reasoning over your real data - {substatus}")

        try:
            result = compute_prospector_score(score_input, on_progress=_update_score_compute_progress)
        except (DraftingNotConfigured, DraftingFailed) as exc:
            score_compute_bar.empty()
            st.error(str(exc))
        else:
            score_compute_bar.progress(1.0, text=":material/check_circle: Done.")
            save_prospector_score(
                result["score"], result["rationale"], result["next_actions"],
                score_input["data_points"], datetime.now(timezone.utc).isoformat(),
            )
            st.toast(f"Prospector Score: {result['score']}/100.", icon=":material/check_circle:")
            st.rerun()

    # Real gap found live 2026-08-09: "disqualified" is sticky by design
    # (see target_accounts.MANUAL_STATUSES) so it never silently changes on
    # its own - but that also meant a company that picked up a real posting
    # AFTER being disqualified (UCB Pharma, BAUSCH, BeOne Medicines USA -
    # BeOne's is now an application under review) sat disqualified forever
    # with nothing flagging the mismatch back. This is a deterministic,
    # zero-cost check (not dependent on the Prospector Score's LLM
    # reasoning noticing it in the data), so it's always visible here
    # rather than gated behind a "Compute Prospector Score" click.
    rereview_flags = find_disqualified_with_new_activity(jobs, applications)
    if rereview_flags:
        lines = [
            f"**{len(rereview_flags)} disqualified account(s) have new activity since being "
            "disqualified - worth a second look.** Status stays disqualified until you change "
            "it yourself; this only flags that evidence exists now that wasn't there when you "
            "disqualified it.",
            "",
        ]
        for flag in rereview_flags:
            job_bits = "; ".join(
                f'"{m["title"]}" at {m["organization"]}' + (f' (application: {m["application_status"]})' if m["application_status"] else "")
                for m in flag["matching_jobs"]
            )
            lines.append(f"- **{flag['company_name']}**: {job_bits}")
        st.warning("\n".join(lines))

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
                with st.container(key="lookup_progress_bar"):
                    lookup_bar = st.progress(0, text=f"Looking up website 1 of {total}: {missing_website[0]['company_name']}...")
                st.html(progress_shimmer_css("lookup_progress_bar"))
                run_cost = 0.0
                for i, acc in enumerate(missing_website, start=1):
                    lookup_bar.progress((i - 1) / total, text=f"Looking up website {i} of {total}: {acc['company_name']}...")
                    found, cost = lookup_company_website(acc["company_name"])
                    set_website(acc["company_name"], found or "")
                    run_cost += cost
                lookup_bar.progress(1.0, text=":material/check_circle: Done.")
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
        "role type, or the reasons you've given for marking something not interested."
    )
    diagnosis_result = st.session_state.get("diagnosis_result")
    if diagnosis_result:
        with st.container(border=True):
            st.markdown(diagnosis_result["narrative"])
            if diagnosis_result["recommendations"]:
                st.markdown("**Worth trying:**")
                for rec in diagnosis_result["recommendations"]:
                    st.markdown(f"- {rec}")
    if st.button("Run rejection-pattern diagnosis", type="primary"):
        diagnosis_input = gather_diagnosis_input(applications, jobs)
        if diagnosis_input["rejected_count"] == 0 and diagnosis_input["not_interested_with_reason_count"] == 0:
            st.info("Nothing to diagnose yet - no rejections tracked and no not-interested reasons given so far.")
        else:
            with st.container(key="diag_progress_bar"):
                diag_bar = st.progress(0, text="Looking for patterns in your rejections...")
            st.html(progress_shimmer_css("diag_progress_bar"))

            def _update_diag_progress(substatus):
                diag_bar.progress(progress_fraction("diagnosis", substatus), text=f"Looking for patterns in your rejections - {substatus}")

            try:
                result = diagnose(diagnosis_input, on_progress=_update_diag_progress)
            except (DraftingNotConfigured, DraftingFailed) as exc:
                diag_bar.empty()
                st.error(str(exc))
            else:
                diag_bar.progress(1.0, text=":material/check_circle: Done.")
                st.session_state["diagnosis_result"] = result
                st.rerun()

    st.subheader("Insights (Learn Engine)")
    st.markdown(
        "Cross-cutting feedback loop over every prediction Panga makes - scoring, target-account "
        "qualification, outreach channel, strategy tags, interview outcomes. "
        "Recommend-only, always - it never changes a score threshold or any setting on its own."
    )
    learn_result = st.session_state.get("learn_engine_result")
    if learn_result:
        with st.container(border=True):
            st.markdown(learn_result["narrative"])
            if learn_result["recommendations"]:
                st.markdown("**Worth considering:**")
                for rec in learn_result["recommendations"]:
                    st.markdown(f"- {rec}")
            for gap in learn_result.get("known_gaps", []):
                st.markdown(f"**Known gap:** {gap}")
    if st.button("Run Learn Engine analysis", type="primary"):
        learn_input = gather_learn_engine_input(applications, jobs, target_accounts, outreach_records, prep_records)
        total_inputs = sum(len(learn_input[k]) for k in ("scoring_vs_outcome", "target_account_vs_outcome", "outreach_vs_outcome", "interview_outcomes"))
        if total_inputs == 0:
            st.info("Nothing to analyze yet - come back once there's more history across scoring, target accounts, outreach, or interviews.")
        else:
            with st.container(key="learn_progress_bar"):
                learn_bar = st.progress(0, text="Reasoning over your real data...")
            st.html(progress_shimmer_css("learn_progress_bar"))

            def _update_learn_progress(substatus):
                learn_bar.progress(progress_fraction("learn", substatus), text=f"Reasoning over your real data - {substatus}")

            try:
                result = analyze_learn_engine(learn_input, on_progress=_update_learn_progress)
            except (DraftingNotConfigured, DraftingFailed) as exc:
                learn_bar.empty()
                st.error(str(exc))
            else:
                learn_bar.progress(1.0, text=":material/check_circle: Done.")
                for gap in learn_input["known_gaps"]:
                    result.setdefault("known_gaps", []).append(gap)
                st.session_state["learn_engine_result"] = result
                st.rerun()

elif active_tab == "prep":
    render_feedback_widget("prep")

    st.header("Interview prep")

    prep_target = st.session_state.get("prep_target")
    if prep_target:
        with st.container(border=True):
            if prep_target["kind"] == "job":
                target_job = next((j for j in jobs if j["source"] == prep_target["source"] and j["job_id"] == prep_target["job_id"]), None)
                st.markdown(f"**Ready to prep: {prep_target['job_label']}**")
            else:
                st.markdown(f"**Ready to prep: \"{prep_target['subject']}\"** from {prep_target['sender']}")
                if prep_target.get("web_link") or prep_target.get("gmail_link"):
                    st.markdown(f"[Open in inbox]({prep_target.get('web_link') or prep_target.get('gmail_link')})")
                job_options = {job_label(j): j for j in jobs}
                chosen_label = st.selectbox(
                    "Which application is this interview for?", ["-- select --"] + list(job_options.keys()),
                    key="prep_email_job_select",
                )
                target_job = job_options.get(chosen_label)

            round_label = st.text_input("Round (e.g. \"Phone screen\", \"Final round\")", value="Round 1", key="prep_round_label")
            interviewer_text = st.text_area(
                "Interviewer(s), one per line - \"Name\" or \"Name - Title\" (optional, leave blank if unknown)",
                key="prep_interviewer_text",
            )
            interviewers = []
            for line in interviewer_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if " - " in line:
                    name, title = line.split(" - ", 1)
                    interviewers.append({"name": name.strip(), "title": title.strip(), "found_via": "manual"})
                else:
                    interviewers.append({"name": line, "title": None, "found_via": "manual"})

            gen_col, clear_col = st.columns([1, 1])
            with gen_col:
                if st.button("Generate prep", type="primary", key="generate_prep_btn", disabled=(target_job is None)):
                    # Real risk (Mirror's proactive sweep, 2026-08-08): this
                    # form's round_label defaults to "Round 1" every time it
                    # opens, so re-clicking Generate for an already-
                    # researched round is one accidental click away, and
                    # save_round() replaces its content wholesale via a
                    # fresh (not guaranteed as-good) web search. Gate behind
                    # a warning only when that round already has real
                    # content ("ready") - a genuinely fresh/in-progress
                    # round still generates immediately, no extra click.
                    existing_record = get_interview_prep(target_job["source"], target_job["job_id"])
                    existing_round = next(
                        (r for r in (existing_record["rounds"] if existing_record else []) if r["round_label"] == round_label),
                        None,
                    )
                    if existing_round is not None and existing_round["status"] != "in_progress":
                        st.session_state["prep_regen_confirm_pending"] = {
                            "target_job": target_job, "round_label": round_label, "interviewers": interviewers,
                        }
                        st.rerun()
                    else:
                        _generate_and_save_prep_round(target_job, round_label, interviewers)
            with clear_col:
                if st.button("Clear", key="clear_prep_target"):
                    st.session_state["prep_target"] = None
                    st.rerun()

    # Checked once here (same pattern as _pass_reason_dialog/
    # regen_confirm_pending elsewhere) rather than inside the button's own
    # if-block, so Streamlit's one-@st.dialog-call-per-run rule is
    # respected regardless of how this section is reached.
    prep_regen_pending = st.session_state.get("prep_regen_confirm_pending")
    if prep_regen_pending:
        _confirm_prep_regen_dialog(
            prep_regen_pending["target_job"], prep_regen_pending["round_label"], prep_regen_pending["interviewers"],
        )

    if not prep_records:
        st.markdown("No interview prep started yet. Use \"Prep for this interview\" on Results or Call to Action once you're past the applied stage.")

    for record in prep_records:
        job = next((j for j in jobs if j["source"] == record["source"] and j["job_id"] == record["job_id"]), None)
        label = job_label(job) if job else f"{record['source']} {record['job_id']}"
        st.subheader(label)

        for round_ in record["rounds"]:
            status_note = "in progress" if round_["status"] == "in_progress" else round_["status"]
            just_generated = st.session_state.pop(
                f"just_generated_prep_{record['source']}_{record['job_id']}_{round_['round_label']}", False,
            )
            # Collapsed while recording an outcome (Mirror's proactive
            # sweep, 2026-08-08): the plain `expanded=(status == "in_progress"
            # or just_generated)` got re-evaluated on every rerun (any
            # widget inside - the outcome selectbox, the notes box -
            # losing focus reruns the script), and without key=+
            # on_change="rerun" Python's `expanded=` argument is
            # authoritative every time, snapping it shut mid-edit (same
            # root cause already fixed on the Results-tab expanders).
            #
            # A plain key=+on_change="rerun" alone isn't quite enough here,
            # unlike the simpler channel/resume-drafted expanders: once a
            # key exists, Streamlit ignores the `expanded=` argument on
            # every later render, so the one-shot just_generated force-open
            # (test_force_expand_is_one_shot_not_sticky_on_a_later_unrelated_rerun,
            # from the earlier 2026-08-06 fine-needle-audit work - a fresh
            # round must NOT stay force-expanded forever just because it
            # was recently generated) would otherwise become permanently
            # sticky the moment the key is set, instead of reverting on the
            # very next render like the un-keyed version correctly did. A
            # second, delayed flag un-forces it exactly one render later -
            # long enough for the force-open to actually render and be
            # seen, short enough to still revert before any real "unrelated
            # rerun" test/interaction happens - restoring the live
            # status == "in_progress" default from that point on, same as
            # if just_generated had never fired.
            round_expander_key = f"prep_round_open_{record['source']}_{record['job_id']}_{round_['round_label']}"
            pending_unforce_key = f"prep_round_unforce_pending_{record['source']}_{record['job_id']}_{round_['round_label']}"
            if st.session_state.pop(pending_unforce_key, False):
                st.session_state[round_expander_key] = round_["status"] == "in_progress"
            if just_generated:
                st.session_state[round_expander_key] = True
                st.session_state[pending_unforce_key] = True
            elif round_expander_key not in st.session_state:
                st.session_state[round_expander_key] = round_["status"] == "in_progress"
            with st.expander(
                f"{round_['round_label']} - {status_note}",
                expanded=st.session_state.get(round_expander_key, False),
                key=round_expander_key,
                on_change="rerun",
            ):
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
                    "How did it go? (feeds the Learn Engine)", OUTCOME_OPTIONS,
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

elif active_tab == "gaps":
    render_feedback_widget("gaps")

    st.header("Profile gaps")
    st.markdown(
        "Real facts your resumes are currently missing that would raise "
        "their ATS score, consolidated across every job in one place - "
        "answer here instead of hunting through each job's Results entry. "
        "A job drops off this list once its questions are answered and its "
        "resume regenerated."
    )

    gap_apps = get_applications_with_open_clarifying_questions()
    if not gap_apps:
        st.markdown("Nothing open right now.")
    else:
        jobs_by_key = {(j.get("source"), j.get("job_id")): j for j in jobs}
        gaps_profile = load_profile()
        for app_record in gap_apps:
            job = jobs_by_key.get((app_record.get("source"), app_record.get("job_id")))
            if job is None:
                continue
            # Real count of what's actually inside (missing-required-
            # keyword questions - item 2 - can add more than what's
            # stored in resume_clarifying_questions alone, the field
            # get_applications_with_open_clarifying_questions() itself
            # still filters on) - computed once here, before the
            # expander label needs it, and passed through so
            # render_analyze_fit_section doesn't redo the same real
            # scoring/keyword-merge pass a second time.
            analysis = _analyze_fit_before_drafting(job, gaps_profile, app_record)
            n = len(analysis["open_questions"])
            score = app_record.get("resume_ats_score")
            score_label = f" - ATS score {score}/100" if score is not None else ""
            # Same key=+on_change="rerun" fix as the Results-tab channel/
            # resume-drafted expanders (Mirror's proactive sweep,
            # 2026-08-08) - this instance was missed since it's rendered
            # from a separate loop (Profile Gaps, not Results). Real data
            # at the time this was found: 19 applications have open
            # clarifying questions (some 6-9 each) - a heavily-used render
            # path, not theoretical. expanded= reads session_state
            # explicitly (not just a bare default) - needed for a
            # programmatic write to take effect, not just a real click.
            # Carried forward onto render_analyze_fit_section (the real
            # backend-wired call that replaced render_gap_questions_section
            # here) when the two branches landed together - the expander
            # persistence fix and the stub-swap are independent changes to
            # the same call site, not a rewrite of either.
            gaps_expander_key = f"profile_gaps_expander_{job.get('source')}_{job.get('job_id')}"
            with st.expander(
                f"{job.get('title', 'Untitled role')} @ {job.get('organization', 'Unknown organization')}{score_label} ({n} open)",
                expanded=st.session_state.get(gaps_expander_key, False),
                key=gaps_expander_key, on_change="rerun",
            ):
                render_analyze_fit_section(job, app_record, analysis=analysis)

    render_answered_gap_questions()

    # Checked once here, after every job's render_analyze_fit_section()
    # call in the loop above has already run - see that function's own
    # docstring for why the dialog can't be called from inside it directly.
    pending_regen_confirm = st.session_state.get("regen_confirm_pending")
    if pending_regen_confirm:
        _confirm_regenerate_dialog(pending_regen_confirm["job"], pending_regen_confirm["cost_info"])

elif active_tab == "linkedin":
    render_feedback_widget("linkedin")

    st.header("LinkedIn profile enhancement")
    st.markdown(
        "Uploads for your LinkedIn profile and connections live in Settings "
        "now, alongside your resume and other source documents. This tab "
        "shows the analysis and suggestions once you've uploaded there and run the "
        "analysis below - it compares your export against your master "
        "profile and target-role skills, and drafts suggested rewrites. "
        "Suggestions below are yours to copy and paste into LinkedIn's own "
        "edit screens."
    )
    if st.button("Go to Settings to upload/update", icon=":material/upload_file:"):
        st.session_state["active_tab"] = "settings"
        st.rerun()

    linkedin_data = load_linkedin_profile()

    if linkedin_data.get("last_saved"):
        st.markdown(f"Last saved: {linkedin_data['last_saved']} (from {', '.join(linkedin_data.get('source_files', []))})")

        if st.button("Analyze profile", type="primary", disabled=not linkedin_data.get("raw_text")):
            with st.container(key="enhance_progress_bar"):
                enhance_bar = st.progress(0, text="Analyzing your LinkedIn profile...")
            st.html(progress_shimmer_css("enhance_progress_bar"))

            def _update_enhance_progress(substatus):
                enhance_bar.progress(progress_fraction("linkedin_enhance", substatus), text=f"Analyzing your LinkedIn profile - {substatus}")

            try:
                context = build_enhancement_context()
                result = analyze_profile(context, on_progress=_update_enhance_progress)
            except (DraftingNotConfigured, DraftingFailed) as exc:
                enhance_bar.empty()
                st.error(str(exc))
            else:
                enhance_bar.progress(1.0, text=":material/check_circle: Done.")
                save_analysis(
                    result["suggestions"], result["profile_strength_score"], result["profile_strength_rationale"],
                    datetime.now(timezone.utc).isoformat(),
                )
                st.toast(f"Profile strength: {result['profile_strength_score']}/100.", icon=":material/check_circle:")
                st.rerun()

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
        st.markdown("Not yet analyzed - upload your profile PDF(s) in Settings, then click \"Analyze profile\" above.")

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
    if render_support_page is None:
        st.info("The Support tab isn't available in this build.")
    else:
        render_support_page(
            BHANGI_PROJECT,
            intro=(
                "Ran into something broken? Describe it below and attach a "
                "screenshot and/or a log file if you have one - this gets queued "
                "for review, same as everything else Panga tracks."
            ),
            project_root=PROJECT_ROOT,
        )
