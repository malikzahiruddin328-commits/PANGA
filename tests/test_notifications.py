"""2026-08-11 (hub-flagged, same fail-open discipline as llm_client's
retry logging): send_notification()'s except clause swallowed
subprocess.SubprocessError/OSError with zero logging, so a failed
notification looked identical to a successful one in every log - the
caller's own daily/CTA run would show no sign anything went wrong.
send_notification() must stay best-effort (never raise, never break the
caller's run over a notification that couldn't be shown) but now logs
the failure via logger.exception() so it's visible in panga_debug.log
instead of vanishing silently.

RM caught a real gap in the first version of this fix: logger.exception()
alone writes nowhere durable in Zahir's actual usage, because "notifications"
wasn't in debug_log.py's _ALWAYS_ON_LOGGER_NAMES tuple and nothing upstream
of gmail_cta_scan.py/job_alert_scan.py's calls to send_notification() ever
triggers debug_log.setup_always_on_error_logging() - the caplog-based tests
below passed regardless, because pytest's caplog attaches its own handler
directly, bypassing the always-on mechanism entirely. Two fixes:
"notifications" added to the tuple, and send_notification() now calls
setup_always_on_error_logging() itself (same self-contained pattern
llm_client.py uses) so it doesn't depend on caller import order.
test_send_notification_failure_reaches_real_panga_debug_log below exercises
the REAL mechanism end to end - no caplog - to prove this actually holds."""

import logging
import subprocess

import pytest

import debug_log
import notifications


def test_send_notification_swallows_subprocess_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise subprocess.SubprocessError("boom")

    monkeypatch.setattr(notifications.subprocess, "run", _raise)
    notifications.send_notification("Title", "Message")  # must not raise


def test_send_notification_swallows_os_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise OSError("no powershell on PATH")

    monkeypatch.setattr(notifications.subprocess, "run", _raise)
    notifications.send_notification("Title", "Message")  # must not raise


def test_send_notification_logs_subprocess_error(monkeypatch, caplog):
    def _raise(*args, **kwargs):
        raise subprocess.SubprocessError("boom")

    monkeypatch.setattr(notifications.subprocess, "run", _raise)
    with caplog.at_level(logging.ERROR, logger="notifications"):
        notifications.send_notification("Panga - Daily job search", "5 new matches")

    assert len(caplog.records) == 1
    assert "Panga - Daily job search" in caplog.records[0].message


def test_send_notification_logs_os_error(monkeypatch, caplog):
    def _raise(*args, **kwargs):
        raise OSError("no powershell on PATH")

    monkeypatch.setattr(notifications.subprocess, "run", _raise)
    with caplog.at_level(logging.ERROR, logger="notifications"):
        notifications.send_notification("Panga - Job alerts", "2 new listings")

    assert len(caplog.records) == 1


def test_send_notification_success_logs_nothing(monkeypatch, caplog):
    monkeypatch.setattr(
        notifications.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a, returncode=0),
    )
    with caplog.at_level(logging.ERROR, logger="notifications"):
        notifications.send_notification("Title", "Message")

    assert len(caplog.records) == 0


def test_send_notification_timeout_is_swallowed_and_logged(monkeypatch, caplog):
    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=10)

    monkeypatch.setattr(notifications.subprocess, "run", _raise)
    with caplog.at_level(logging.ERROR, logger="notifications"):
        notifications.send_notification("Title", "Message")  # must not raise

    assert len(caplog.records) == 1


def _cleanup_handlers(logger, before_handlers):
    for handler in list(logger.handlers):
        if handler not in before_handlers:
            logger.removeHandler(handler)
            handler.close()


def test_send_notification_failure_reaches_real_panga_debug_log(isolated_data, monkeypatch):
    # The real gap RM caught: exercise debug_log's actual always-on
    # mechanism end to end, no caplog involved, matching how a real
    # scheduled script (gmail_cta_scan.py, job_alert_scan.py - neither of
    # which imports llm_client/fulfillment first) would hit this.
    monkeypatch.delenv("PANGA_DEBUG", raising=False)
    monkeypatch.setattr(debug_log, "_always_on_configured", False)
    logger = logging.getLogger("notifications")
    before = list(logger.handlers)
    try:
        assert "notifications" in debug_log._ALWAYS_ON_LOGGER_NAMES

        def _raise(*args, **kwargs):
            raise subprocess.SubprocessError("boom")

        monkeypatch.setattr(notifications.subprocess, "run", _raise)
        notifications.send_notification("Panga - Job alerts", "3 new listings")

        for handler in logger.handlers:
            handler.flush()
        content = debug_log.LOG_PATH.read_text(encoding="utf-8")
        assert "Failed to show system-tray notification" in content
        assert "Panga - Job alerts" in content
    finally:
        _cleanup_handlers(logger, before)
