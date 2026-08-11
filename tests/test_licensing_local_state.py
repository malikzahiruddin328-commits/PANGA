from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def isolated_license_paths(tmp_path, monkeypatch):
    import licensing.local_state as local_state
    import licensing.device_id as device_id

    monkeypatch.setattr(local_state, "STATE_PATH", tmp_path / "license_state.json")
    monkeypatch.setattr(device_id, "DEVICE_ID_PATH", tmp_path / "device_id.json")
    return local_state, device_id


def test_grace_state_before_any_checkin_is_never_verified(isolated_license_paths):
    local_state, _ = isolated_license_paths
    assert local_state.grace_state()["state"] == "never_verified"


def test_grace_state_just_after_checkin_is_grace(isolated_license_paths):
    local_state, _ = isolated_license_paths
    now = datetime.now(timezone.utc)
    local_state.record_successful_checkin({"status": "verified"}, now=now)

    result = local_state.grace_state(now=now + timedelta(hours=1))
    assert result["state"] == "grace"
    assert result["days_left"] == pytest.approx(3.0 - 1 / 24, abs=1e-6)


def test_grace_state_at_exactly_three_days_is_locked(isolated_license_paths):
    local_state, _ = isolated_license_paths
    now = datetime.now(timezone.utc)
    local_state.record_successful_checkin({"status": "verified"}, now=now)

    result = local_state.grace_state(now=now + timedelta(days=3))
    assert result["state"] == "locked"


def test_grace_state_just_under_three_days_is_still_grace(isolated_license_paths):
    local_state, _ = isolated_license_paths
    now = datetime.now(timezone.utc)
    local_state.record_successful_checkin({"status": "verified"}, now=now)

    result = local_state.grace_state(now=now + timedelta(days=3) - timedelta(seconds=1))
    assert result["state"] == "grace"
    assert result["days_left"] > 0


def test_expired_entitlement_stays_expired_on_subsequent_network_failure(isolated_license_paths):
    # Regression for the "grace leaks onto confirmed-expired customers" bug:
    # a real successful check-in that itself reported "expired" must not be
    # reinterpreted as grace-eligible just because time-since-checkin is
    # small. The whole point of "expired" is that it's authoritative.
    local_state, _ = isolated_license_paths
    now = datetime.now(timezone.utc)
    local_state.record_successful_checkin(
        {"status": "expired", "reason": "subscription", "expires_at": "2026-08-01T00:00:00Z"}, now=now,
    )

    result = local_state.grace_state(now=now + timedelta(minutes=1))
    assert result["state"] == "expired_subscription"
    assert result["expires_at"] == "2026-08-01T00:00:00Z"


def test_expired_trial_stays_expired_on_subsequent_network_failure(isolated_license_paths):
    local_state, _ = isolated_license_paths
    now = datetime.now(timezone.utc)
    local_state.record_successful_checkin(
        {"status": "expired", "reason": "trial", "expires_at": "2026-08-01T00:00:00Z"}, now=now,
    )

    result = local_state.grace_state(now=now + timedelta(days=10))
    assert result["state"] == "expired_trial"


def test_verified_entitlement_still_gets_normal_grace_treatment(isolated_license_paths):
    # Make sure the fix didn't break the intended fail-open path for an
    # actually-verified customer hitting a connectivity gap.
    local_state, _ = isolated_license_paths
    now = datetime.now(timezone.utc)
    local_state.record_successful_checkin({"status": "verified", "expires_at": "2027-01-01T00:00:00Z"}, now=now)

    result = local_state.grace_state(now=now + timedelta(hours=1))
    assert result["state"] == "grace"


def test_clock_skew_does_not_produce_negative_days_offline(isolated_license_paths):
    local_state, _ = isolated_license_paths
    now = datetime.now(timezone.utc)
    local_state.record_successful_checkin({"status": "verified"}, now=now)

    # "now" earlier than the recorded check-in (e.g. system clock adjusted).
    result = local_state.grace_state(now=now - timedelta(hours=1))
    assert result["state"] == "grace"
    assert result["days_left"] == pytest.approx(3.0, abs=1e-6)


def test_session_round_trip(isolated_license_paths):
    local_state, _ = isolated_license_paths
    local_state.save_session("access-1", "refresh-1", "zahir@example.com")
    session = local_state.get_session()
    assert session == {"access_token": "access-1", "refresh_token": "refresh-1", "email": "zahir@example.com"}

    local_state.clear_session()
    assert local_state.get_session() is None


def test_device_id_is_stable_across_calls(isolated_license_paths):
    _, device_id = isolated_license_paths
    first = device_id.get_device_id()
    second = device_id.get_device_id()
    assert first == second
