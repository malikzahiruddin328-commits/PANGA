import requests
import pytest


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    import licensing.local_state as local_state
    import licensing.device_id as device_id
    import licensing.client as client

    monkeypatch.setattr(local_state, "STATE_PATH", tmp_path / "license_state.json")
    monkeypatch.setattr(device_id, "DEVICE_ID_PATH", tmp_path / "device_id.json")
    monkeypatch.setenv("PANGA_LICENSE_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("PANGA_LICENSE_SUPABASE_ANON_KEY", "anon-key")
    local_state.save_session("access-1", "refresh-1", "zahir@example.com")
    return client, local_state


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.ok = 200 <= status_code < 300
        self.text = str(body)

    def json(self):
        return self._body


def test_check_in_verified_persists_local_state(isolated_client, monkeypatch):
    client, local_state = isolated_client

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url.endswith("/functions/v1/license-status")
        return _FakeResponse(200, {"status": "verified", "expires_at": "2027-01-01T00:00:00Z"})

    monkeypatch.setattr(requests, "post", fake_post)

    result = client.check_in()
    assert result == {"state": "verified", "expires_at": "2027-01-01T00:00:00Z"}
    assert local_state.load()["last_known_entitlement"]["status"] == "verified"


def test_check_in_expired_trial(isolated_client, monkeypatch):
    client, _ = isolated_client

    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(200, {"status": "expired", "reason": "trial", "expires_at": "2026-01-01T00:00:00Z"})

    monkeypatch.setattr(requests, "post", fake_post)

    result = client.check_in()
    assert result["state"] == "expired_trial"


def test_check_in_network_failure_falls_back_to_grace(isolated_client, monkeypatch):
    client, local_state = isolated_client
    local_state.record_successful_checkin({"status": "verified"})

    def fake_post(url, headers=None, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(requests, "post", fake_post)

    result = client.check_in()
    assert result["state"] == "grace"


def test_check_in_with_no_session_is_signed_out(tmp_path, monkeypatch):
    import licensing.local_state as local_state
    import licensing.client as client

    monkeypatch.setattr(local_state, "STATE_PATH", tmp_path / "license_state.json")
    result = client.check_in()
    assert result == {"state": "signed_out"}


def test_ensure_activated_auto_activates_on_fresh_device(isolated_client, monkeypatch):
    client, _ = isolated_client
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if url.endswith("/license-status") and len(calls) == 1:
            return _FakeResponse(409, "no active device binding")
        if url.endswith("/device-activate"):
            return _FakeResponse(200, {"activated": True, "device_id": "d1"})
        if url.endswith("/license-status"):
            return _FakeResponse(200, {"status": "verified", "expires_at": "2027-01-01T00:00:00Z"})
        raise AssertionError(f"unexpected call to {url}")

    monkeypatch.setattr(requests, "post", fake_post)

    result = client.ensure_activated_and_check_in()
    assert result["state"] == "verified"
    assert any(url.endswith("/device-activate") for url in calls)


def test_ensure_activated_surfaces_device_conflict(isolated_client, monkeypatch):
    client, _ = isolated_client

    def fake_post(url, headers=None, json=None, timeout=None):
        if url.endswith("/license-status"):
            return _FakeResponse(409, "no active device binding")
        if url.endswith("/device-activate"):
            return _FakeResponse(409, "already activated on another device")
        raise AssertionError(f"unexpected call to {url}")

    monkeypatch.setattr(requests, "post", fake_post)

    result = client.ensure_activated_and_check_in()
    assert result["state"] == "device_conflict"
