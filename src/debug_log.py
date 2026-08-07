"""Optional debug-mode logging (2026-08-03) - built for the friend-testing
package (run_app_friend_test.bat) so a tester who hits something broken can
just send back one log file instead of describing/screenshotting a console
window that scrolls away. No-op unless PANGA_DEBUG=1 is set, so it never
runs in Zahir's own normal usage.

Two things this adds on top of what Streamlit already shows in-browser
(full tracebacks for anything Streamlit itself catches):
1. A rotating file log (data/logs/panga_debug.log) at DEBUG level, so
   anything the app itself logs - not just crashes - is captured.
2. A sys.excepthook that logs any exception that somehow escapes both
   Streamlit's own error boundary and normal try/except handling, as a
   last-resort safety net rather than a silent process exit.
"""

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "data" / "logs" / "panga_debug.log"

_configured = False


def is_debug_mode() -> bool:
    return os.environ.get("PANGA_DEBUG") == "1"


def setup_debug_logging() -> None:
    """Idempotent - safe to call on every Streamlit rerun (app.py calls
    this at import time, which happens on every rerun)."""
    global _configured
    if not is_debug_mode() or _configured:
        return
    _configured = True

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    logging.info("Debug logging started (PANGA_DEBUG=1)")

    def _log_uncaught(exc_type, exc_value, exc_tb):
        logging.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _log_uncaught
