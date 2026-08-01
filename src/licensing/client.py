"""HTTP client for the license service (see ../../license-service/).

Orchestrates the three-way split documented in docs/licensing-scope.md:
  - a live call that reaches the server is always authoritative
    ("verified" or "expired_trial"/"expired_subscription")
  - a live call that fails to reach the server at all falls back to purely
    local grace-period math (local_state.grace_state) - "grace" for the
    first 3 days, "locked" after
  - a live call that reaches the server but is answered with a definite
    business error (e.g. device not activated) is neither of the above -
    it's surfaced as LicenseServiceError so the caller can react
    specifically (e.g. prompt device activation), not lumped in with
    "offline"
"""

import os
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

from licensing import local_state
from licensing.device_id import get_device_id

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

_DEFAULT_TIMEOUT = 10
_UNINSTALL_TIMEOUT = 3  # must not hang or fail the uninstaller if offline


class LicenseNetworkError(Exception):
    """Could not reach the license service at all (offline, DNS, timeout)."""


class LicenseServiceError(Exception):
    """The service was reached and returned an error response."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    return bool(os.environ.get("PANGA_LICENSE_SUPABASE_URL")) and bool(
        os.environ.get("PANGA_LICENSE_SUPABASE_ANON_KEY")
    )


def _supabase_url() -> str:
    url = os.environ.get("PANGA_LICENSE_SUPABASE_URL")
    if not url:
        raise RuntimeError("PANGA_LICENSE_SUPABASE_URL is not set - see .env.example")
    return url.rstrip("/")


def _anon_key() -> str:
    key = os.environ.get("PANGA_LICENSE_SUPABASE_ANON_KEY")
    if not key:
        raise RuntimeError("PANGA_LICENSE_SUPABASE_ANON_KEY is not set - see .env.example")
    return key


# ---- Auth (Supabase's own email-OTP endpoints, not a custom function) ----

def request_login_code(email: str) -> None:
    """Triggers Supabase Auth to email a one-time code to `email`. Same
    call whether this is the customer's first time or their hundredth -
    matches the scope doc's "no login-vs-signup fork" onboarding."""
    resp = requests.post(
        f"{_supabase_url()}/auth/v1/otp",
        headers={"apikey": _anon_key(), "Content-Type": "application/json"},
        json={"email": email, "create_user": True},
        timeout=_DEFAULT_TIMEOUT,
    )
    if not resp.ok:
        raise LicenseServiceError(resp.text, resp.status_code)


def verify_login_code(email: str, code: str) -> None:
    """Exchanges the emailed code for a session, and persists it locally
    via local_state so subsequent calls (including headless background
    checks) don't need another interactive login."""
    resp = requests.post(
        f"{_supabase_url()}/auth/v1/verify",
        headers={"apikey": _anon_key(), "Content-Type": "application/json"},
        json={"email": email, "token": code, "type": "email"},
        timeout=_DEFAULT_TIMEOUT,
    )
    if not resp.ok:
        raise LicenseServiceError(resp.text, resp.status_code)
    body = resp.json()
    local_state.save_session(body["access_token"], body["refresh_token"], email)


def _refresh_session() -> Optional[dict]:
    session = local_state.get_session()
    if session is None:
        return None
    resp = requests.post(
        f"{_supabase_url()}/auth/v1/token?grant_type=refresh_token",
        headers={"apikey": _anon_key(), "Content-Type": "application/json"},
        json={"refresh_token": session["refresh_token"]},
        timeout=_DEFAULT_TIMEOUT,
    )
    if not resp.ok:
        return None
    body = resp.json()
    local_state.save_session(body["access_token"], body["refresh_token"], session["email"])
    return local_state.get_session()


# ---- Edge Function calls ----

def _call_function(name: str, body: dict, timeout: float = _DEFAULT_TIMEOUT, _retried: bool = False) -> dict:
    session = local_state.get_session()
    if session is None:
        raise LicenseServiceError("Not signed in", 401)

    try:
        resp = requests.post(
            f"{_supabase_url()}/functions/v1/{name}",
            headers={
                "Authorization": f"Bearer {session['access_token']}",
                "apikey": _anon_key(),
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise LicenseNetworkError(str(e)) from e

    if resp.status_code == 401 and not _retried:
        # Access token likely expired - refresh once and retry, rather than
        # treating an expired token as "can't verify" (that would put a
        # customer with a perfectly good subscription into the grace-period
        # UI for no reason).
        if _refresh_session() is not None:
            return _call_function(name, body, timeout=timeout, _retried=True)

    if not resp.ok:
        raise LicenseServiceError(resp.text, resp.status_code)
    return resp.json()


def start_trial() -> dict:
    return _call_function("trial-start", {})


def activate_device() -> dict:
    return _call_function("device-activate", {"device_fingerprint": get_device_id()})


def release_device(via: str = "self_service") -> dict:
    timeout = _UNINSTALL_TIMEOUT if via == "uninstall" else _DEFAULT_TIMEOUT
    return _call_function(
        "device-release",
        {"device_fingerprint": get_device_id(), "via": via},
        timeout=timeout,
    )


def create_checkout_session(success_url: str, cancel_url: str) -> dict:
    return _call_function("billing-create-checkout-session", {"success_url": success_url, "cancel_url": cancel_url})


def create_portal_session(return_url: str) -> dict:
    return _call_function("billing-portal-session", {"return_url": return_url})


def check_in() -> dict:
    """The one call the UI actually needs on launch + daily background
    check. Returns one of:
      {"state": "verified", "expires_at": ...}
      {"state": "expired_trial" | "expired_subscription", "expires_at": ...}
      {"state": "grace", "days_left": float}
      {"state": "locked", "days_offline": float}
      {"state": "never_verified"}   -- no successful check-in has ever happened
      {"state": "device_not_activated"}
      {"state": "signed_out"}       -- no session at all, route to onboarding
    """
    if local_state.get_session() is None:
        return {"state": "signed_out"}

    try:
        entitlement = _call_function("license-status", {"device_fingerprint": get_device_id()})
    except LicenseNetworkError:
        return local_state.grace_state()
    except LicenseServiceError as e:
        if e.status_code == 409:
            return {"state": "device_not_activated"}
        # Any other reachable-server error (e.g. a different device holds
        # the binding, 403) still counts as "the server answered" - but
        # there's no clean entitlement to report, so fall back to the last
        # known-good local answer rather than inventing a new UI state.
        return local_state.grace_state()

    local_state.record_successful_checkin(entitlement)
    if entitlement["status"] == "verified":
        return {"state": "verified", "expires_at": entitlement.get("expires_at")}
    return {
        "state": f"expired_{entitlement['reason']}",
        "expires_at": entitlement.get("expires_at"),
    }


def ensure_activated_and_check_in() -> dict:
    """The actual entry point the UI calls. Wraps check_in() with one extra
    step: a fresh install/new device gets auto-activated transparently
    (the customer never sees "device not activated" as an error) unless
    another device already holds the binding, in which case that's
    surfaced distinctly so the UI can point at self-service release
    instead of lumping it in with offline/grace states."""
    result = check_in()
    if result["state"] != "device_not_activated":
        return result

    try:
        activate_device()
    except LicenseNetworkError:
        return local_state.grace_state()
    except LicenseServiceError as e:
        if e.status_code == 409:
            return {"state": "device_conflict"}
        raise

    return check_in()
