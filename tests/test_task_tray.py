"""2026-08-19: covers task_tray.py's pure logic (pidfile reading, status
labels, icon image generation) - not pystray's own event loop/tray
rendering, which needs a real desktop session and isn't something this
suite runs against. Live tray rendering is a manual smoke-test, same as
any other GUI surface in this codebase.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import task_tray  # noqa: E402


def test_read_tracked_pid_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(task_tray, "PID_FILE", tmp_path / "panga.pid")
    assert task_tray._read_tracked_pid() is None


def test_read_tracked_pid_reads_real_value(tmp_path, monkeypatch):
    pid_file = tmp_path / "panga.pid"
    pid_file.write_text("12345\n", encoding="utf-8")
    monkeypatch.setattr(task_tray, "PID_FILE", pid_file)
    assert task_tray._read_tracked_pid() == 12345


def test_read_tracked_pid_corrupt_content_returns_none(tmp_path, monkeypatch):
    # A partially-written pidfile (e.g. caught mid-write) must never crash
    # the tray - "can't tell" is the same as "not running" here, not an
    # exception that takes the whole tray icon down.
    pid_file = tmp_path / "panga.pid"
    pid_file.write_text("not-a-pid", encoding="utf-8")
    monkeypatch.setattr(task_tray, "PID_FILE", pid_file)
    assert task_tray._read_tracked_pid() is None


def test_production_is_running_true_when_pid_alive(tmp_path, monkeypatch):
    pid_file = tmp_path / "panga.pid"
    pid_file.write_text("999", encoding="utf-8")
    monkeypatch.setattr(task_tray, "PID_FILE", pid_file)
    monkeypatch.setattr(task_tray, "is_pid_alive", lambda pid: pid == 999)
    assert task_tray._production_is_running() is True


def test_production_is_running_false_when_pid_dead(tmp_path, monkeypatch):
    pid_file = tmp_path / "panga.pid"
    pid_file.write_text("999", encoding="utf-8")
    monkeypatch.setattr(task_tray, "PID_FILE", pid_file)
    monkeypatch.setattr(task_tray, "is_pid_alive", lambda pid: False)
    assert task_tray._production_is_running() is False


def test_status_label_running_shows_real_pid(tmp_path, monkeypatch):
    pid_file = tmp_path / "panga.pid"
    pid_file.write_text("777", encoding="utf-8")
    monkeypatch.setattr(task_tray, "PID_FILE", pid_file)
    assert task_tray._status_label(True) == "Panga: Running (PID 777)"


def test_status_label_not_running():
    assert task_tray._status_label(False) == "Panga: Not running"


def test_active_tasks_label_empty():
    assert task_tray._active_tasks_label([]) == "No active tasks"


def test_active_tasks_label_no_stalled():
    tasks = [{"stalled": False}, {"stalled": False}]
    assert task_tray._active_tasks_label(tasks) == "2 active task(s)"


def test_active_tasks_label_some_stalled():
    tasks = [{"stalled": True}, {"stalled": False}, {"stalled": True}]
    assert task_tray._active_tasks_label(tasks) == "3 active task(s), 2 STALLED"


def test_make_icon_image_running_vs_stopped_differ():
    running_img = task_tray._make_icon_image(running=True, active_count=0)
    stopped_img = task_tray._make_icon_image(running=False, active_count=0)
    assert running_img.size == (64, 64)
    assert stopped_img.size == (64, 64)
    # Different colors for running vs stopped - real distinguishing signal,
    # not just cosmetic sameness with different labels.
    assert list(running_img.getdata()) != list(stopped_img.getdata())


def test_make_icon_image_active_count_adds_badge():
    no_badge = task_tray._make_icon_image(running=True, active_count=0)
    with_badge = task_tray._make_icon_image(running=True, active_count=3)
    assert list(no_badge.getdata()) != list(with_badge.getdata())
