"""System tray Task Monitor (2026-08-19, Zahir's ask from 2026-08-17,
finally built): a native Windows tray icon giving at-a-glance visibility
into whether Panga's production process is actually running, and what
it's actively doing right now - without needing the browser tab open.

Two real, independently-checked pieces of state, reusing existing
mechanisms rather than reimplementing them:

1. Is production itself running? Reads panga.pid (written by run_app.ps1,
   2026-08-18's restart-reliability fix - see that file's own module
   comment for the real incident that made a tracked PID necessary at
   all) and checks liveness via tailoring.task_monitor.is_pid_alive() -
   the exact same tasklist-based check the in-app Task Monitor tab
   already uses, so this tray icon and that tab can never silently
   disagree about what "alive" means.
2. What's actively in flight right now? tailoring.task_monitor.
   get_active_tasks() - the same real, PID-verified activity feed behind
   the Task Monitor tab (subscription rounds, the paid final-build lock),
   including its own stalled-detection. This tray icon is a different
   VIEW onto the same real data, not a second source of truth.

Deliberately reuses run_app.ps1's actual restart mechanism for its own
"Restart" menu action (invokes it as a subprocess) rather than
reimplementing kill/relaunch logic a third time in a third place - one
real restart implementation, three ways to trigger it (double-click the
shortcut, this tray icon, or by hand).

Run standalone: `venv\\Scripts\\python.exe src\\task_tray.py` (or launch
it alongside run_app.bat - see docs/task-tray-setup.md for the "start
both automatically" convention once written). Polls every
POLL_INTERVAL_SECONDS; pystray's own event loop handles the actual icon
rendering/menu, this module only computes what to show.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tailoring.task_monitor import get_active_tasks, is_pid_alive  # noqa: E402

PID_FILE = PROJECT_ROOT / "panga.pid"
RUN_APP_PS1 = PROJECT_ROOT / "run_app.ps1"
APP_URL = "http://localhost:8510"
POLL_INTERVAL_SECONDS = 10


def _read_tracked_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _production_is_running() -> bool:
    return is_pid_alive(_read_tracked_pid())


def _make_icon_image(running: bool, active_count: int) -> Image.Image:
    """Solid-color dot: green = running, gray = stopped/unknown - no icon
    file dependency, drawn fresh each refresh so it's trivial to also
    reflect active_count later (e.g. a small badge) without needing an
    asset pipeline for a single-purpose internal tool."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (46, 160, 67, 255) if running else (128, 128, 128, 255)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color)
    if active_count > 0:
        # Small orange badge in the corner when something's actively
        # in flight - the "is it actually doing something right now"
        # signal Zahir asked for, visible without opening the menu.
        badge_color = (255, 140, 0, 255)
        draw.ellipse((size - 26, size - 26, size - 2, size - 2), fill=badge_color)
    return img


def _open_app(icon=None, item=None):
    import webbrowser
    webbrowser.open(APP_URL)


def _restart_app(icon=None, item=None):
    # Reuses the real restart mechanism (run_app.ps1) rather than a
    # second kill/relaunch implementation - see this module's own
    # docstring. Runs detached so the tray icon doesn't block on it.
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RUN_APP_PS1)],
        cwd=str(PROJECT_ROOT),
        creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
    )


def _stop_app(icon=None, item=None):
    pid = _read_tracked_pid()
    if pid and is_pid_alive(pid):
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)


def _exit_tray(icon, item=None):
    icon.stop()


def _status_label(running: bool) -> str:
    pid = _read_tracked_pid()
    if running:
        return f"Panga: Running (PID {pid})"
    return "Panga: Not running"


def _active_tasks_label(tasks: list[dict]) -> str:
    if not tasks:
        return "No active tasks"
    stalled = sum(1 for t in tasks if t.get("stalled"))
    if stalled:
        return f"{len(tasks)} active task(s), {stalled} STALLED"
    return f"{len(tasks)} active task(s)"


def _build_menu(running: bool, tasks: list[dict]) -> pystray.Menu:
    task_items = [
        pystray.MenuItem(
            f"  {t['title']} @ {t['organization']} - {t['status']}"
            + (" [STALLED]" if t.get("stalled") else ""),
            None, enabled=False,
        )
        for t in tasks[:8]  # cap the menu length - Zahir's own basket batch runs 6 concurrent max
        # (BASKET_DRAFTALL_MAX_WORKERS in ui/app.py), so 8 rows covers any
        # real batch with room to spare without a tray menu growing unbounded
    ]
    return pystray.Menu(
        pystray.MenuItem(_status_label(running), None, enabled=False),
        pystray.MenuItem(_active_tasks_label(tasks), None, enabled=False),
        pystray.Menu.SEPARATOR,
        *task_items,
        pystray.Menu.SEPARATOR if task_items else pystray.MenuItem("", None, enabled=False, visible=False),
        pystray.MenuItem("Open Panga", _open_app),
        pystray.MenuItem("Restart", _restart_app),
        pystray.MenuItem("Stop", _stop_app, enabled=running),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit tray monitor", _exit_tray),
    )


def _refresh_loop(icon: pystray.Icon):
    while icon.visible:
        running = _production_is_running()
        tasks = get_active_tasks() if running else []
        icon.icon = _make_icon_image(running, len(tasks))
        icon.title = _status_label(running) + " - " + _active_tasks_label(tasks)
        icon.menu = _build_menu(running, tasks)
        time.sleep(POLL_INTERVAL_SECONDS)


def main():
    running = _production_is_running()
    tasks = get_active_tasks() if running else []
    icon = pystray.Icon(
        "panga_task_monitor",
        _make_icon_image(running, len(tasks)),
        _status_label(running),
        _build_menu(running, tasks),
    )
    threading.Thread(target=_refresh_loop, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
