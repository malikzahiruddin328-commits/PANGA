"""Real gap found live 2026-08-09: Zahir hit a genuine Claude API call
failure (an out-of-credits billing error) that collapsed into a generic
"Something went wrong" message, and General had to reproduce the failing
API call manually with a raw script just to learn what actually happened -
llm_client.py's own detailed error logging only reached disk when
PANGA_DEBUG=1, which "never runs in Zahir's own normal usage" (this
module's own long-standing docstring). setup_always_on_error_logging()
below is the fix: a real llm_client call failure always gets written to
data/logs/panga_debug.log, with no PANGA_DEBUG gate, while the existing
full-DEBUG-level logging (setup_debug_logging()) stays exactly as it was.
"""

import logging

import debug_log


def _llm_logger():
    return logging.getLogger("llm_client")


def _cleanup_handlers(logger, before_handlers):
    for handler in list(logger.handlers):
        if handler not in before_handlers:
            logger.removeHandler(handler)
            handler.close()


def test_always_on_error_logging_captures_an_error_with_no_debug_flag_set(isolated_data, monkeypatch):
    monkeypatch.delenv("PANGA_DEBUG", raising=False)
    monkeypatch.setattr(debug_log, "_always_on_configured", False)
    logger = _llm_logger()
    before = list(logger.handlers)
    try:
        debug_log.setup_always_on_error_logging()
        logger.error("Claude API call failed: credit balance too low")
        for handler in logger.handlers:
            handler.flush()
        content = debug_log.LOG_PATH.read_text(encoding="utf-8")
        assert "credit balance too low" in content
    finally:
        _cleanup_handlers(logger, before)


def test_always_on_error_logging_ignores_debug_and_info_level_messages(isolated_data, monkeypatch):
    # Deliberately narrower than setup_debug_logging()'s full verbosity -
    # only a real failure (ERROR+) should ever reach this always-on file,
    # so normal usage stays quiet in it.
    monkeypatch.delenv("PANGA_DEBUG", raising=False)
    monkeypatch.setattr(debug_log, "_always_on_configured", False)
    logger = _llm_logger()
    before = list(logger.handlers)
    try:
        debug_log.setup_always_on_error_logging()
        logger.info("Just routine info, not a failure.")
        logger.debug("Just routine debug output.")
        for handler in logger.handlers:
            handler.flush()
        content = debug_log.LOG_PATH.read_text(encoding="utf-8") if debug_log.LOG_PATH.exists() else ""
        assert "Just routine" not in content
    finally:
        _cleanup_handlers(logger, before)


def test_always_on_error_logging_is_idempotent(isolated_data, monkeypatch):
    monkeypatch.delenv("PANGA_DEBUG", raising=False)
    monkeypatch.setattr(debug_log, "_always_on_configured", False)
    logger = _llm_logger()
    before = list(logger.handlers)
    try:
        debug_log.setup_always_on_error_logging()
        debug_log.setup_always_on_error_logging()
        debug_log.setup_always_on_error_logging()
        added = [h for h in logger.handlers if h not in before]
        assert len(added) == 1
    finally:
        _cleanup_handlers(logger, before)
