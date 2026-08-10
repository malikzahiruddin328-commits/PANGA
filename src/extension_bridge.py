"""Local HTTP listener for the Panga browser extension (extension/, 2026-08-08).

The extension (LinkedIn/Dice job-page detection + "Send to Panga") talks to
this process over plain localhost HTTP - there's no separate service to
install or keep running, it's a background thread inside whichever
Streamlit process imports this module. Two endpoints:

- POST /heartbeat - the extension pings this continuously (via a
  chrome.alarms tick, not a page-bound timer, so it keeps firing even with
  no matching tab focused) so Panga can tell "is the extension actually
  installed and running" independent of whatever page is open.
- POST /capture - the extension posts the JD text (+ title/company/url) the
  user captured via the popup's "Send to Panga" button.

Binds a FIXED port (PANGA_EXTENSION_BRIDGE_PORT env var, default 8765) once
per process. Every Streamlit instance that imports this module (prod 8510,
RM's 8509, manual dev slots 8501-8508) calls start_server() - only ONE can
actually hold the port at a time; the rest fail the bind and just run
without a listener of their own (see start_server() docstring for why
that's correct, not a bug to work around). Separate from the
8501-8510 Streamlit port range in .claude/launch.json/CLAUDE.md since
this isn't a Streamlit dev server - documented in CLAUDE.md's port
convention section so another session doesn't collide with 8765 for
something unrelated.

TESTING GOTCHA (found live 2026-08-09): if production (port 8510) is
already running when you spin up a dev instance to test this module, your
dev instance's start_server() silently fails to bind 8765 (correct,
documented behavior above) and every POST /heartbeat or /capture you send
to 127.0.0.1:8765 lands in PRODUCTION's in-memory state instead - not
your dev instance's, and not visible in whatever you're looking at. This
happened for real during this feature's own verification: test captures
briefly polluted Zahir's actual running app before the port-collision was
noticed. Always set PANGA_EXTENSION_BRIDGE_PORT to an unused port (and
point curl/test scripts at that same port) when testing this module
against anything other than production itself - never assume 8765 is
"yours" just because your own process didn't error.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8765
# The extension pings every ~30s (chrome.alarms' minimum period on most
# Chrome builds); allow one missed tick before calling it "gone" rather than
# flickering red on ordinary timer jitter.
HEARTBEAT_STALE_SECONDS = 90
# A capture older than this is almost certainly a stale/abandoned tab the
# user isn't actually trying to send right now, not today's JD - don't
# auto-fill against it.
CAPTURE_TTL_SECONDS = 30 * 60

_lock = threading.Lock()
_state = {
    "last_heartbeat_ts": None,
    "last_heartbeat_source": None,
    "captures": {},  # normalized_url -> {title, company, description, source, url, ts}
}
_server_started = False
_bound_port = None


def _normalize_url(url: str) -> str:
    """Strips query string/fragment so a LinkedIn tracking-param variant or
    a re-visit of the same Dice posting still matches the job record's own
    stored posting_url (see job_store.add_manual_job's job_id derivation,
    which has the same "URLs vary, the real posting doesn't" concern)."""
    if not url:
        return ""
    url = url.split("#", 1)[0].split("?", 1)[0]
    return url.rstrip("/").lower()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # don't spam Streamlit's own console with every heartbeat tick

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        # The extension's background service worker fetches with
        # host_permissions (CORS-exempt), so this is defense-in-depth, not
        # load-bearing - harmless either way.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_POST(self):
        if self.path == "/heartbeat":
            body = self._read_json_body()
            with _lock:
                _state["last_heartbeat_ts"] = time.time()
                _state["last_heartbeat_source"] = body.get("source")
            self._send_json(200, {"ok": True})
            return

        if self.path == "/capture":
            body = self._read_json_body()
            url = (body.get("url") or "").strip()
            description = (body.get("description") or "").strip()
            if not url or not description:
                self._send_json(400, {"ok": False, "error": "url and description are required"})
                return
            with _lock:
                _state["captures"][_normalize_url(url)] = {
                    "title": (body.get("title") or "").strip(),
                    "company": (body.get("company") or "").strip(),
                    "description": description,
                    "source": (body.get("source") or "").strip(),
                    "url": url,
                    "ts": time.time(),
                }
                _prune_captures_locked()
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"ok": False, "error": "not found"})


class _Server(ThreadingHTTPServer):
    # Real Windows-specific bug, confirmed live 2026-08-09: stdlib's
    # HTTPServer/ThreadingHTTPServer default allow_reuse_address to True,
    # which sets SO_REUSEADDR before bind. On Linux that mostly just skips
    # the TIME_WAIT delay when rebinding a recently-closed socket - but on
    # Windows, SO_REUSEADDR lets a SECOND, genuinely live listener bind the
    # exact same address:port a first one already holds, with no OSError
    # at all. That breaks this whole module's design, which assumes a
    # second process's bind attempt fails cleanly so it can no-op (see
    # start_server()'s docstring) - instead both processes end up
    # LISTENING, and incoming connections route to whichever one Windows
    # happens to favor (observed: the most-recently-bound one),
    # non-deterministically and invisibly to either process.
    #
    # Reproduced live: two separate real OS processes both showed
    # LISTENING on the identical port via netstat simultaneously with the
    # stdlib default, and repeated requests all silently landed on the
    # second process - reflecting exactly what happened when RM manually
    # ran a live-verification Streamlit instance alongside production and
    # Zahir saw "the extension isn't working" (captures were routing to
    # RM's ephemeral instance, not always production). With
    # allow_reuse_address = False, the same two-process repro instead gave
    # the second process a real OSError ([WinError 10048] Only one usage
    # of each socket address...) - restoring the "only one real holder,
    # everyone else gets a clean, catchable failure" guarantee the rest of
    # this module already assumes.
    allow_reuse_address = False


def _prune_captures_locked() -> None:
    """Caller must already hold _lock. Bounds memory for a Panga process
    left running for days - captures are cheap but not free, and nothing
    else ever removes an entry."""
    cutoff = time.time() - CAPTURE_TTL_SECONDS
    stale_keys = [key for key, value in _state["captures"].items() if value["ts"] < cutoff]
    for key in stale_keys:
        del _state["captures"][key]


def start_server() -> None:
    """Idempotent - safe to call on every Streamlit rerun (app.py calls it
    at import time, which happens on every rerun, same pattern as
    debug_log.setup_debug_logging()). A bind failure (port already held by
    another Panga process) is expected, not an error to surface - it just
    means this process runs without its own listener, and its status
    indicator will correctly show "not detected" rather than a heartbeat it
    never actually received itself. Runs the server on a daemon thread so
    it never blocks Streamlit's own shutdown.

    PANGA_EXTENSION_BRIDGE_PORT="0" requests an OS-assigned ephemeral port
    (standard socket behavior for port 0) instead of a fixed one - use
    get_bound_port() afterward to find out which port was actually bound.
    This is what this module's own test suite uses (see
    tests/test_extension_bridge.py) - a hardcoded fixed test port turned
    out to be a REAL bug found live 2026-08-09: under pytest-randomly, an
    orphaned process from an earlier interrupted test run was still
    listening on the hardcoded port, this function's bind silently failed
    against it (correct, documented behavior above), but _server_started
    still got set True - so a fresh test process had no way to tell it was
    silently talking to a STALE process's state instead of its own,
    producing 5 confusing, order-dependent failures that had nothing to do
    with actual thread-safety inside this module. Binding to an
    OS-assigned port per test process makes that whole class of collision
    structurally impossible, rather than just harder to hit."""
    global _server_started, _bound_port
    if _server_started:
        return
    _server_started = True
    port = int(os.environ.get("PANGA_EXTENSION_BRIDGE_PORT", DEFAULT_PORT))
    try:
        server = _Server(("127.0.0.1", port), _Handler)
    except OSError:
        return
    _bound_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="panga-extension-bridge")
    thread.start()


def get_bound_port() -> int | None:
    """The port this process's own server actually bound, or None if it
    never successfully bound one - either start_server() was never called,
    or the bind failed (most likely another process already holds the
    configured port; see that function's docstring). A caller that needs
    to KNOW it's genuinely talking to ITS OWN in-process server (rather
    than assuming PANGA_EXTENSION_BRIDGE_PORT unconditionally worked)
    should check this instead of assuming."""
    return _bound_port


def get_heartbeat_status() -> dict:
    """Read-side for the UI's status indicator. `connected` reflects this
    process's own view only - see module docstring on why a non-owning
    process correctly shows disconnected rather than borrowing another
    process's state."""
    with _lock:
        ts = _state["last_heartbeat_ts"]
        source = _state["last_heartbeat_source"]
    if ts is None:
        return {"connected": False, "seconds_ago": None, "source": None}
    age = time.time() - ts
    return {"connected": age <= HEARTBEAT_STALE_SECONDS, "seconds_ago": age, "source": source}


def get_capture_for_url(url: str) -> dict | None:
    """Returns the captured {title, company, description, source, url, ts}
    for this exact posting (matched via the job record's own posting_url,
    normalized the same way captures are stored), or None if nothing was
    captured for it, or what was captured is older than CAPTURE_TTL_SECONDS."""
    if not url:
        return None
    with _lock:
        capture = _state["captures"].get(_normalize_url(url))
        if capture is None:
            return None
        if time.time() - capture["ts"] > CAPTURE_TTL_SECONDS:
            return None
        return dict(capture)
