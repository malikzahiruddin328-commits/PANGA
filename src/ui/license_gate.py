"""License verification UI: the top-right indicator plus the two full-
screen blocking states. See docs/licensing-scope.md "UI, three distinct
states" (plus signed-out/device-conflict, which that doc's four states
don't explicitly cover but the app still has to render something for).

Kept out of app.py per the existing code-organization convention (see
ui/feedback_widget.py's docstring) - app.py is already large.

Network cost: the actual check-in call only fires once per Streamlit
session (cached in st.session_state) plus on explicit Refresh clicks -
Streamlit reruns this whole module on every widget interaction, so hitting
the license service on every single click would be wasteful and slow (see
CLAUDE.md "design with performance in mind"). The daily background check
happens out-of-process via scripts/license_daily_checkin.py instead.
"""

import math

import streamlit as st

from licensing import client

_SESSION_KEY = "license_check_result"


def _run_check_in() -> None:
    st.session_state[_SESSION_KEY] = client.ensure_activated_and_check_in()


def render_indicator_and_get_block() -> dict | None:
    """Renders the small top-right indicator for the non-blocking states
    (verified / grace) in whatever column the caller is already inside.
    Returns None if the app should proceed normally, or the check-in
    result dict if the caller should render a full-screen block and stop."""
    if not client.is_configured():
        return {"state": "not_configured"}

    if _SESSION_KEY not in st.session_state:
        _run_check_in()

    result = st.session_state[_SESSION_KEY]
    state = result["state"]

    if state == "verified":
        st.markdown("✓ License verified")
        return None

    if state == "grace":
        days_left = math.ceil(result["days_left"])
        ind_col, refresh_col = st.columns([3, 1])
        with ind_col:
            st.markdown(f"⚠ License unverified — {days_left} day(s) left")
        with refresh_col:
            if st.button("Refresh", key="license_refresh_grace", width="stretch"):
                _run_check_in()
                st.rerun()
        return None

    return result


def render_block_screen(result: dict) -> None:
    """Full-screen blocking UI for every other state. Caller must st.stop()
    right after this - it does not stop itself, so it stays trivially
    testable (renders into whatever container it's called from)."""
    state = result["state"]

    if state == "not_configured":
        st.error(
            "The license service isn't configured yet (PANGA_LICENSE_SUPABASE_URL / "
            "PANGA_LICENSE_SUPABASE_ANON_KEY missing from .env) - see license-service/README.md.",
            icon=":material/settings:",
        )
        return

    if state in ("locked", "never_verified"):
        st.error(
            "We haven't been able to verify your license in 3 days — "
            "connect to the internet and reopen Panga.\n\n"
            "This is a verification problem, not a billing one — nothing "
            "about your subscription has changed.",
            icon=":material/wifi_off:",
        )
        if st.button("Refresh", key="license_refresh_locked"):
            _run_check_in()
            st.rerun()
        return

    if state in ("expired_trial", "expired_subscription"):
        expires_at = result.get("expires_at", "")
        noun = "trial" if state == "expired_trial" else "subscription"
        st.error(
            f"Your {noun} ended on {expires_at}. Renew to keep using Panga.",
            icon=":material/lock:",
        )
        st.link_button("Renew now", "https://panga.example.com/renew")
        return

    if state == "device_conflict":
        st.error(
            "This license is already active on another device. Deactivate "
            "it from that device (Settings → Deactivate this device), or "
            "contact support if it's lost or unavailable.",
            icon=":material/devices:",
        )
        if st.button("Refresh", key="license_refresh_conflict"):
            _run_check_in()
            st.rerun()
        return

    if state == "signed_out":
        _render_email_signin()
        return

    if state == "needs_anthropic_connect":
        _render_anthropic_connect_placeholder()
        return

    st.error(f"Unexpected license state: {state}")


def _render_email_signin() -> None:
    st.markdown("Enter your email to continue.")
    email = st.text_input("Email", key="license_signin_email", label_visibility="collapsed")
    if st.button("Continue", key="license_signin_start") and email:
        try:
            client.request_login_code(email)
            st.session_state["license_signin_pending_email"] = email
            st.rerun()
        except client.LicenseServiceError as e:
            st.error(f"Couldn't send a login code: {e}")

    pending_email = st.session_state.get("license_signin_pending_email")
    if pending_email:
        st.markdown(f"We emailed a code to **{pending_email}**.")
        code = st.text_input("Code", key="license_signin_code", label_visibility="collapsed")
        if st.button("Verify", key="license_signin_verify") and code:
            try:
                client.verify_login_code(pending_email, code)
                client.start_trial()
                st.session_state[_SESSION_KEY] = {"state": "needs_anthropic_connect"}
                st.rerun()
            except client.LicenseServiceError as e:
                st.error(f"That code didn't work: {e}")


def _render_anthropic_connect_placeholder() -> None:
    # Draft copy from docs/licensing-scope.md "Onboarding UX" (refine, not
    # final). The two buttons are NOT wired up yet — this depends on
    # native-packaging's direct-API work (own Anthropic key, no live
    # Claude Code session dependency), which is still in progress in that
    # branch's worktree as of this writing. Flagging rather than guessing
    # at where the key actually gets stored/validated; that's
    # native-packaging's llm_client module to define.
    st.markdown(
        "Panga uses Claude AI to do the actual reading and writing — "
        "you'll need your own account with Anthropic (the company that "
        "makes it), so you're only ever charged for what you personally "
        "use, never marked up by us."
    )
    c1, c2 = st.columns(2)
    with c1:
        st.button("Connect an account I already have", key="anthropic_connect_existing", disabled=True)
    with c2:
        st.button("I don't have one — set one up", key="anthropic_connect_new", disabled=True)
    st.caption("Coming soon — waiting on native-packaging's direct-API support.")
    if st.button("Skip for now", key="anthropic_connect_skip"):
        _run_check_in()
        st.rerun()
