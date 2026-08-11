"""2026-08-11 (hub-flagged, same fail-open discipline as llm_client's
retry logging): send_notification()'s except clause swallowed
subprocess.SubprocessError/OSError with zero logging, so a failed
notification looked identical to a successful one in every log - the
caller's own daily/CTA run would show no sign anything went wrong.
send_notification() must stay best-effort (never raise, never break the
caller's run over a notification that couldn't be shown) but now logs
the failure via logger.exception() so it's visible in panga_debug.log
instead of vanishing silently."""

import logging
import subprocess

import pytest

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
