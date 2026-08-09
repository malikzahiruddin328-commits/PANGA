"""Tests for extension_bridge.py's heartbeat/capture bridge (2026-08-09).

Uses a dedicated test-only port (not the real default, 8765) so this suite
never depends on - or interferes with - whatever else may already be
listening on the real port (production, a dev instance, ...). Set BEFORE
extension_bridge is imported, since start_server() only reads the env var
once (the first, idempotent, call). See extension_bridge.py's own "TESTING
GOTCHA" docstring note for why this matters - found live via exactly this
collision (a manual verification pass briefly hit production) during this
feature's own development.

No isolated_data fixture needed (see tests/README.md) - this module has no
on-disk store, its whole state is an in-memory dict guarded by a lock.
"""

import os
import time

os.environ["PANGA_EXTENSION_BRIDGE_PORT"] = "18765"

import pytest
import requests

import extension_bridge

BASE_URL = "http://127.0.0.1:18765"


@pytest.fixture(autouse=True)
def _running_server_with_clean_state():
    """Starts the bridge server once for the whole test session (module-
    level globals mean a second start_server() call is a correct no-op, not
    a fresh instance - see its own docstring), and resets in-memory state
    before each test so tests can't see each other's heartbeats/captures."""
    extension_bridge.start_server()
    # start_server() is async (binds on a daemon thread) - give it a moment
    # to actually be listening before the first test fires a real request.
    for _ in range(20):
        try:
            requests.options(BASE_URL + "/heartbeat", timeout=0.5)
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    with extension_bridge._lock:
        extension_bridge._state["last_heartbeat_ts"] = None
        extension_bridge._state["last_heartbeat_source"] = None
        extension_bridge._state["captures"] = {}
    yield


def test_heartbeat_status_before_any_heartbeat_is_disconnected():
    status = extension_bridge.get_heartbeat_status()
    assert status == {"connected": False, "seconds_ago": None, "source": None}


def test_heartbeat_post_marks_connected():
    resp = requests.post(BASE_URL + "/heartbeat", json={"source": "extension"}, timeout=2)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    status = extension_bridge.get_heartbeat_status()
    assert status["connected"] is True
    assert status["source"] == "extension"
    assert 0 <= status["seconds_ago"] < 2


def test_heartbeat_older_than_stale_threshold_is_disconnected():
    with extension_bridge._lock:
        extension_bridge._state["last_heartbeat_ts"] = (
            time.time() - extension_bridge.HEARTBEAT_STALE_SECONDS - 1
        )
        extension_bridge._state["last_heartbeat_source"] = "extension"

    status = extension_bridge.get_heartbeat_status()
    assert status["connected"] is False


def test_capture_then_lookup_by_exact_url():
    resp = requests.post(
        BASE_URL + "/capture",
        json={
            "url": "https://www.dice.com/job-detail/abc-123",
            "title": "CIO",
            "company": "Acme Corp",
            "description": "Full job description text.",
            "source": "dice",
        },
        timeout=2,
    )
    assert resp.status_code == 200

    capture = extension_bridge.get_capture_for_url("https://www.dice.com/job-detail/abc-123")
    assert capture is not None
    assert capture["title"] == "CIO"
    assert capture["company"] == "Acme Corp"
    assert capture["description"] == "Full job description text."
    assert capture["source"] == "dice"


def test_capture_lookup_normalizes_query_string_trailing_slash_and_case():
    requests.post(
        BASE_URL + "/capture",
        json={
            "url": "https://www.dice.com/job-detail/abc-123",
            "description": "Full job description text.",
        },
        timeout=2,
    )

    # A job record's own stored posting_url and the extension's captured
    # tab URL are very unlikely to be byte-identical (tracking params,
    # trailing slash, casing) - this is the exact matching path
    # render_paste_jd_prompt_before_drafting() relies on.
    variants = [
        "https://www.dice.com/job-detail/abc-123?utm_source=claude-user&utm_medium=mcp",
        "https://www.dice.com/job-detail/abc-123/",
        "https://WWW.DICE.COM/job-detail/abc-123",
        "https://www.dice.com/job-detail/abc-123#some-anchor",
    ]
    for url in variants:
        assert extension_bridge.get_capture_for_url(url) is not None, url


def test_capture_lookup_for_unrelated_url_returns_none():
    requests.post(
        BASE_URL + "/capture",
        json={
            "url": "https://www.dice.com/job-detail/abc-123",
            "description": "Full job description text.",
        },
        timeout=2,
    )
    assert extension_bridge.get_capture_for_url("https://www.dice.com/job-detail/completely-different") is None


def test_capture_missing_url_is_rejected_and_not_stored():
    resp = requests.post(
        BASE_URL + "/capture",
        json={"url": "", "description": "Full job description text."},
        timeout=2,
    )
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_capture_missing_description_is_rejected_and_not_stored():
    resp = requests.post(
        BASE_URL + "/capture",
        json={"url": "https://www.dice.com/job-detail/abc-123", "description": "   "},
        timeout=2,
    )
    assert resp.status_code == 400
    assert extension_bridge.get_capture_for_url("https://www.dice.com/job-detail/abc-123") is None


def test_capture_older_than_ttl_is_treated_as_absent():
    requests.post(
        BASE_URL + "/capture",
        json={
            "url": "https://www.dice.com/job-detail/abc-123",
            "description": "Full job description text.",
        },
        timeout=2,
    )
    with extension_bridge._lock:
        key = extension_bridge._normalize_url("https://www.dice.com/job-detail/abc-123")
        extension_bridge._state["captures"][key]["ts"] = (
            time.time() - extension_bridge.CAPTURE_TTL_SECONDS - 1
        )

    assert extension_bridge.get_capture_for_url("https://www.dice.com/job-detail/abc-123") is None


def test_prune_captures_locked_removes_stale_entries_from_the_dict():
    """Distinct from the TTL test above: that one confirms a stale capture
    is treated as absent on READ; this confirms _prune_captures_locked()
    actually deletes it from the dict (the memory-bounding mechanism, not
    just the read-side filter) - both matter, since a store growing
    unboundedly on a long-running production process is exactly the kind
    of thing that gets missed by testing only the read-side behavior."""
    with extension_bridge._lock:
        extension_bridge._state["captures"]["stale-key"] = {
            "title": "", "company": "", "description": "x", "source": "dice",
            "url": "https://example.com/stale", "ts": time.time() - extension_bridge.CAPTURE_TTL_SECONDS - 1,
        }

    # Any successful /capture triggers a prune pass as a side effect.
    requests.post(
        BASE_URL + "/capture",
        json={"url": "https://www.dice.com/job-detail/fresh", "description": "fresh capture"},
        timeout=2,
    )

    with extension_bridge._lock:
        assert "stale-key" not in extension_bridge._state["captures"]


def test_unknown_path_returns_404():
    resp = requests.post(BASE_URL + "/not-a-real-endpoint", json={}, timeout=2)
    assert resp.status_code == 404


def test_options_preflight_returns_204_with_cors_headers():
    resp = requests.options(BASE_URL + "/capture", timeout=2)
    assert resp.status_code == 204
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"


def test_start_server_is_idempotent():
    # Calling start_server() again (e.g. every Streamlit rerun in the real
    # app) must not raise or double-bind the port.
    extension_bridge.start_server()
    extension_bridge.start_server()
    resp = requests.post(BASE_URL + "/heartbeat", json={"source": "extension"}, timeout=2)
    assert resp.status_code == 200
