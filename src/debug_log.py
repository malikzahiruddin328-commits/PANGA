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

setup_always_on_error_logging() below is a THIRD, separate mechanism, NOT
gated behind PANGA_DEBUG (2026-08-09, real gap Zahir hit live): a genuine
Claude API call failure (Zahir's real case - an out-of-credits billing
error) collapsed into the same generic "Something went wrong talking to
Claude" message with the real cause nowhere retrievable, since this
file's own full-DEBUG logging "never runs in Zahir's own normal usage"
(the line above, still true for that mechanism) - General had to
reproduce the failing API call manually with a raw script just to learn
what actually happened. A real drafting/scoring/extraction call failure
is significant enough that Zahir shouldn't need debug mode on to have it
captured, so this narrower mechanism (llm_client's own logger only, at
ERROR level, not full-DEBUG verbosity for everything) writes to the SAME
panga_debug.log file always, while the full-DEBUG-level logging above
stays exactly as it was, still behind PANGA_DEBUG, for friend-testing.
"""

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "data" / "logs" / "panga_debug.log"

_configured = False
_always_on_configured = False


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


def setup_always_on_error_logging() -> None:
    """Always active, regardless of PANGA_DEBUG - see module docstring.
    Attaches a dedicated handler to llm_client's own logger only (not the
    root logger setup_debug_logging() above configures), at ERROR level,
    so normal usage stays quiet in this file except for a real API call
    failure worth diagnosing later. Writes to the same panga_debug.log
    path as the full-DEBUG mechanism - two independent FileHandlers on
    the same path is safe here (this is a single-process Streamlit app,
    and each handler's own writes are already line-buffered/flushed by
    the standard logging module), matching this file's existing no-extra-
    locking convention for a diagnostic text log (unlike the real JSON
    data stores under data/, which do use security.file_lock).
    Idempotent - safe to call on every Streamlit rerun."""
    global _always_on_configured
    if _always_on_configured:
        return
    _always_on_configured = True

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    # Deliberately does NOT call llm_client_logger.setLevel() - that would
    # set the LOGGER's own effective threshold, which would silence its
    # DEBUG/INFO calls even under PANGA_DEBUG=1's full-verbosity mode
    # (a logger's own level gates BEFORE any handler ever sees the
    # record). Filtering only on the HANDLER (above) is enough to keep
    # this mechanism to ERROR+ without touching what other handlers -
    # like setup_debug_logging()'s root-logger one - are allowed to see.
    logging.getLogger("llm_client").addHandler(handler)
