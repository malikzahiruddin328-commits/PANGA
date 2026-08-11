"""Local, encrypted cache of licensing state.

This is deliberately dumb storage only - it knows nothing about whether a
live check-in succeeded or failed; that orchestration lives in
`licensing.client`. All it does is:
  - persist the Supabase Auth session so headless calls (daily background
    check, the uninstaller) don't need an interactive login every time
  - remember the last time a check-in actually reached the server and what
    it said, so `grace_state()` can answer "how long have we been unable
    to verify" purely from local data when the live call fails

Storage location: `data/security/`, alongside crypto_store.py's other
encrypted stores. NOTE (native-packaging / update-mechanism dependency,
see docs/licensing-scope.md): this path needs to survive an in-place
update without being wiped or orphaned - not yet confirmed with the
update-mechanism branch.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from security import crypto_store

from licensing._file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = PROJECT_ROOT / "data" / "security" / "license_state.json"

GRACE_PERIOD_DAYS = 3

_DEFAULT_STATE = {
    "session": None,  # {"access_token", "refresh_token", "email"}
    "last_successful_checkin_at": None,  # ISO 8601, UTC
    "last_known_entitlement": None,  # {"status": "verified"|"expired", "reason": "trial"|"subscription", "expires_at"}
}


def _load_raw() -> dict:
    return crypto_store.read_json(STATE_PATH, default=dict(_DEFAULT_STATE))


def load() -> dict:
    return _load_raw()


def save_session(access_token: str, refresh_token: str, email: str) -> None:
    with locked(STATE_PATH):
        state = _load_raw()
        state["session"] = {"access_token": access_token, "refresh_token": refresh_token, "email": email}
        crypto_store.write_json(STATE_PATH, state)


def clear_session() -> None:
    with locked(STATE_PATH):
        state = _load_raw()
        state["session"] = None
        crypto_store.write_json(STATE_PATH, state)


def get_session() -> Optional[dict]:
    return _load_raw().get("session")


def record_successful_checkin(entitlement: dict, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    with locked(STATE_PATH):
        state = _load_raw()
        state["last_successful_checkin_at"] = now.isoformat()
        state["last_known_entitlement"] = entitlement
        crypto_store.write_json(STATE_PATH, state)


def grace_state(now: Optional[datetime] = None) -> dict:
    """Purely local derivation of "can we still trust the last known-good
    answer" - only meaningful when a *live* check-in attempt just failed
    (see licensing.client.check_in). Never called after a successful live
    check-in, since that always returns an authoritative verified/expired
    answer instead (docs/licensing-scope.md state 4 requires "confirmed via
    a real successful check-in" - this function only ever produces states
    2 and 3, "can't verify").

    The 3-day grace window exists to protect a legitimately verified
    customer from a transient connectivity gap - it must NOT also extend
    to a customer whose last successful check-in already confirmed they're
    expired. Without this guard, a confirmed-expired customer regains up
    to 3 more days of access on nothing more than a subsequent network
    blip or backend error (client.py routes every non-409 failure through
    this function, not just true offline cases) - found in adversarial
    review 2026-08-10, see the licensing session's audit report.
    """
    now = now or datetime.now(timezone.utc)
    state = _load_raw()

    last_entitlement = state.get("last_known_entitlement")
    if last_entitlement is not None and last_entitlement.get("status") == "expired":
        return {
            "state": f"expired_{last_entitlement.get('reason', 'subscription')}",
            "expires_at": last_entitlement.get("expires_at"),
        }

    last_checkin_raw = state.get("last_successful_checkin_at")
    if last_checkin_raw is None:
        return {"state": "never_verified"}

    last_checkin = datetime.fromisoformat(last_checkin_raw)
    days_offline = (now - last_checkin).total_seconds() / 86400
    if days_offline < 0:
        days_offline = 0  # clock skew - don't report a negative "days offline"

    if days_offline >= GRACE_PERIOD_DAYS:
        return {"state": "locked", "days_offline": days_offline}

    days_left = GRACE_PERIOD_DAYS - days_offline
    return {"state": "grace", "days_left": days_left}
