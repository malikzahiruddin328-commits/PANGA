"""Tests for extension_bridge.py's heartbeat/capture bridge (2026-08-09).

Uses PANGA_EXTENSION_BRIDGE_PORT="0" - an OS-assigned ephemeral port, not a
hardcoded one - so this suite can NEVER collide with anything else, ever,
regardless of what else happens to be running. This replaced an earlier
version hardcoded to port 18765, after a REAL bug it caused: under
`pytest -p randomly`, an orphaned process left over from an earlier
interrupted test run was still listening on 18765; extension_bridge's own
start_server() correctly detected the bind was taken and no-opped, but
(a real gap in that function, fixed alongside this) `_server_started` was
set True regardless of whether the bind actually succeeded, so this test
process had no way to tell it wasn't talking to its own server - every
POST silently landed in the STALE process's state instead of this one's,
producing 5 confusing, order-dependent failures with no real thread-safety
bug behind them. An ephemeral port makes the whole class of collision
structurally impossible rather than just less likely - see
extension_bridge.py's start_server()/get_bound_port() docstrings for the
full account.

Random-order hunting: `pytest -p randomly --randomly-seed=<N>` (plain
`pytest` stays deterministic - see pyproject.toml's addopts).

No isolated_data fixture needed (see tests/README.md) - this module has no
on-disk store, its whole state is an in-memory dict guarded by a lock.
"""

import os
import time

os.environ["PANGA_EXTENSION_BRIDGE_PORT"] = "0"

import pytest
import requests

import extension_bridge

extension_bridge.start_server()
_bound_port = extension_bridge.get_bound_port()
if _bound_port is None:
    # Should be structurally impossible (port 0 always finds a free port) -
    # fail loud at collection time rather than let every test below
    # silently misbehave the way the hardcoded-port version did.
    raise RuntimeError(
        "extension_bridge.start_server() did not bind an OS-assigned "
        "ephemeral port (PANGA_EXTENSION_BRIDGE_PORT=0) - see "
        "get_bound_port()'s docstring."
    )
BASE_URL = f"http://127.0.0.1:{_bound_port}"

# start_server() binds synchronously (get_bound_port() is available the
# moment it returns) but serve_forever() itself starts on a daemon thread -
# give that thread a moment to actually be accepting connections before the
# very first real test fires a request. One-time, at collection, not
# per-test (the earlier version's per-test poll was needless overhead once
# the server's been ready for a while).
for _ in range(20):
    try:
        requests.options(BASE_URL + "/heartbeat", timeout=0.5)
        break
    except requests.ConnectionError:
        time.sleep(0.1)
else:
    raise RuntimeError(f"extension_bridge's test server never became reachable at {BASE_URL}")


@pytest.fixture(autouse=True)
def _clean_state():
    """Resets in-memory state before each test so tests can't see each
    other's heartbeats/captures - the server itself (module-level globals,
    see start_server()'s docstring) is started once above, not per-test."""
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


def test_server_does_not_allow_address_reuse():
    # Real bug, confirmed live 2026-08-09: stdlib's ThreadingHTTPServer
    # defaults allow_reuse_address to True, which sets SO_REUSEADDR before
    # bind. On Windows (unlike Linux) that lets a SECOND process bind the
    # exact same address:port a first one already holds - no OSError, both
    # genuinely LISTENING, incoming connections routing to whichever one
    # Windows favors. Reproduced with two real separate OS processes: this
    # is exactly what happened when RM ran a manual live-verification
    # Streamlit instance alongside production and captures silently
    # started routing to RM's instance instead of always production. This
    # whole module's design (start_server()'s docstring: "only ONE can
    # actually hold the port... the rest fail the bind and just no-op")
    # depends entirely on a second bind attempt failing cleanly - a plain
    # attribute check here is cheap insurance against someone later
    # "simplifying" _Server back to bare ThreadingHTTPServer and silently
    # reintroducing this.
    assert extension_bridge._Server.allow_reuse_address is False
