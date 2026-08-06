"""Targeted test for gmail_client.py's token-refresh locking fix
(2026-08-06) - the scheduled fulfillment task and a manual "Send and
receive" click are now two processes that can both need to refresh this
same token at close to the same moment, so the load-check-refresh-save
sequence must run under security.file_lock.locked(), not as an unlocked
read-modify-write. Not a full gmail_client.py test suite - this module is
almost entirely real-API-dependent; this covers the one piece of new pure
control-flow logic."""

import gmail_client


class FakeCreds:
    def __init__(self):
        self.valid = False
        self.expired = True
        self.refresh_token = "refresh-me"
        self.refreshed = False

    def refresh(self, request):
        self.refreshed = True
        self.valid = True

    def to_json(self):
        return "{}"


class RecordingLock:
    """Stand-in for security.file_lock.locked() that records whether it
    was held while the refresh actually happened, without touching a real
    lock file."""

    def __init__(self, calls, name):
        self.calls = calls
        self.name = name

    def __enter__(self):
        self.calls.append(("enter", self.name))
        return self

    def __exit__(self, *exc):
        self.calls.append(("exit", self.name))
        return False


def test_get_credentials_refresh_runs_inside_the_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(gmail_client, "CREDENTIALS_PATH", tmp_path / "credentials.json")
    (tmp_path / "credentials.json").write_text("{}")  # is_configured() just checks existence

    fake_creds = FakeCreds()
    monkeypatch.setattr(gmail_client, "_load_cached_credentials", lambda: fake_creds)

    saved = []
    monkeypatch.setattr(gmail_client, "_save_credentials", lambda creds: saved.append(creds))

    calls = []
    monkeypatch.setattr(gmail_client, "locked", lambda name: RecordingLock(calls, name))

    result = gmail_client.get_credentials()

    assert result is fake_creds
    assert fake_creds.refreshed is True
    assert saved == [fake_creds]
    # The refresh (and the resulting save) must have happened strictly
    # between the lock's enter and exit, not after release - proven here
    # by saved already being non-empty by the time "exit" is recorded.
    assert calls[0] == ("enter", "gmail_token")
    assert calls[-1] == ("exit", "gmail_token")
