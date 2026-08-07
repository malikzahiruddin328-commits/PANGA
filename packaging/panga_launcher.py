"""Unified entry point for the packaged Panga desktop app (PyInstaller, see
packaging/panga.spec). Deliberately outside src/ and scripts/ - this is
packaging glue, not application logic, and imports the real application
code rather than reimplementing anything.

One exe, four modes, dispatched by argv[1]:
  (no args)            - GUI mode. Spawns itself as `--serve`, waits for the
                          local Streamlit server to come up, then shows it
                          in a pywebview window. This is what the Start
                          Menu/Desktop shortcuts point at.
  --serve --port N      - runs the Streamlit server in this process's own
                          main thread and blocks. Not for direct use -
                          bootstrap.run() registers a SIGTERM handler that
                          only works on a real main thread, so the GUI mode
                          above runs it as a child process instead of a
                          background thread (confirmed empirically while
                          building this - a plain threading.Thread raises
                          "signal only works in main thread of the main
                          interpreter").
  --task <name>         - headless mode for Windows Task Scheduler. <name>
                          is one of: run-search, gmail-scan, cta-fulfillment
                          (see docs/native-packaging-task-scheduler.md).
                          No pywebview window.
  --uninstall-helper     - run by the Inno Setup uninstaller (see
                          packaging/panga.iss) before it deletes any files:
                          data-retention prompt, backup offer, license
                          device-release call, in the order fixed by
                          docs/native-packaging-scope.md ("Uninstall
                          behavior").

Artifact layout this assumes (see docs/native-packaging-scope.md, "Artifact
layout" - PyInstaller --onedir, default layout with the _internal/
subfolder):
    Panga\\Panga.exe
    Panga\\_internal\\...            <- PyInstaller's own bundle contents
    Panga\\config\\                  <- copied in by the installer, not PyInstaller
    Panga\\.streamlit\\               <- same
    Panga\\data\\                    <- created at first run, user data
    Panga\\.env                      <- optional, same shape as dev's .env
    Panga\\scripts\\uninstall_scheduled_tasks.ps1   <- shipped as-is for the uninstaller

No changes to src/ or scripts/ were needed for any of this: every module
there computes its own PROJECT_ROOT as `Path(__file__).resolve().parents[N]`,
and PyInstaller's default onedir layout happens to preserve exactly the
same directory nesting depth those modules already assume (a top-level
src/foo.py-style module and a frozen _internal/foo.py-style module are both
"one directory below the app root"; a two-deep src/pkg/foo.py and a frozen
_internal/pkg/foo.py are both "two directories below" - _internal/ plays
the same structural role src/ played in the dev checkout). Verified by
building a throwaway PyInstaller bundle of stand-in modules using the same
parents[N] pattern and printing where they resolved.
"""

import ctypes
import os
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def _add_dev_paths() -> None:
    """Dev-mode only (`python packaging/panga_launcher.py ...` while
    building/testing) - put src/ and scripts/ on sys.path the same way
    run_app.bat and scripts/*.py already do. Frozen builds don't need this;
    PyInstaller's own import machinery already finds every module it
    collected, regardless of sys.path."""
    if FROZEN:
        return
    repo_root = Path(__file__).resolve().parent.parent
    for sub in ("src", "scripts"):
        p = str(repo_root / sub)
        if p not in sys.path:
            sys.path.insert(0, p)


_add_dev_paths()


def _app_root() -> Path:
    """The directory that holds config/, .streamlit/, data/, .env, and (in
    dev mode) src/ and scripts/ - the exe's own directory when frozen, the
    repo root otherwise."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_dir() -> Path:
    """Where the collected src/ modules actually live: sys._MEIPASS
    (PyInstaller onedir sets this to the _internal/ folder itself, not a
    temp extraction - confirmed empirically) when frozen, repo root/src
    otherwise."""
    if FROZEN:
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "src"


def _self_command() -> list:
    """argv prefix to re-invoke this same launcher as a subprocess."""
    if FROZEN:
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve())]


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_server(url: str, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _error_box(title: str, message: str) -> None:
    # ctypes MessageBoxW rather than tkinter here - this can fire before
    # the Streamlit server (and anything heavier) is up, and needs no
    # imports beyond the stdlib to report a startup failure.
    ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR


# ---- --serve ----

def cmd_serve(port: int) -> None:
    from streamlit import config as st_config
    from streamlit.web import bootstrap

    app_path = str(_bundle_dir() / "ui" / "app.py")
    st_config.set_option("server.headless", True)
    st_config.set_option("server.port", port)
    st_config.set_option("server.address", "127.0.0.1")
    st_config.set_option("browser.gatherUsageStats", False)
    st_config.set_option("global.developmentMode", False)
    # Real app data (jobs.json, applications.json, ...) lives under
    # _app_root()/data via each module's own PROJECT_ROOT logic, not CWD -
    # this is just so any incidental relative-path usage lands somewhere
    # sane instead of wherever the OS happened to launch the exe from.
    os.chdir(_app_root())
    bootstrap.run(app_path, is_hello=False, args=[], flag_options={})


# ---- GUI mode ----

def cmd_gui() -> None:
    import webview

    port = _find_free_port()
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    proc = subprocess.Popen(
        _self_command() + ["--serve", "--port", str(port)],
        cwd=str(_app_root()),
        creationflags=creationflags,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        if not _wait_for_server(url):
            _error_box("Panga", "Panga's app server didn't start in time. Try again, and check Task Manager for a stuck Panga.exe process.")
            return
        webview.create_window("Panga", url, width=1440, height=900, min_size=(900, 600))
        webview.start()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---- --task (Windows Task Scheduler) ----

_TASKS = {
    "run-search": "run_search",
    "gmail-scan": "gmail_cta_scan",
    "cta-fulfillment": "cta_fulfillment",
}


def cmd_task(name: str) -> int:
    module_name = _TASKS.get(name)
    if module_name is None:
        sys.stderr.write(f"Unknown task '{name}'. Valid: {', '.join(_TASKS)}\n")
        return 2
    os.chdir(_app_root())
    import importlib

    module = importlib.import_module(module_name)
    module.run()
    return 0


# ---- --uninstall-helper ----

def _uninstall_ask_data_retention() -> bool:
    """Returns True if the customer chose to remove their data. "Keep" is
    the default/highlighted choice per docs/native-packaging-scope.md."""
    import tkinter as tk

    choice = {"remove": False}
    root = tk.Tk()
    root.title("Panga - Uninstall")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    tk.Label(
        root,
        text=(
            "Panga also stores your job search data on this computer\n"
            "(resume, job matches, application history, and the insights\n"
            "it's learned from your outcomes).\n\n"
            "Keep this data, or remove it too?"
        ),
        justify="left",
        padx=20,
    ).pack(pady=(20, 12))

    button_row = tk.Frame(root)
    button_row.pack(pady=(0, 20), padx=20)

    def pick(remove: bool) -> None:
        choice["remove"] = remove
        root.destroy()

    keep_btn = tk.Button(button_row, text="Keep my data  (recommended)", command=lambda: pick(False), padx=12, pady=6, default="active")
    keep_btn.pack(side="left", padx=(0, 10))
    tk.Button(button_row, text="Remove my data too", command=lambda: pick(True), padx=12, pady=6).pack(side="left")

    root.bind("<Return>", lambda e: pick(False))
    root.protocol("WM_DELETE_WINDOW", lambda: pick(False))  # closing the window = keep (safer default)
    keep_btn.focus()
    root.mainloop()
    return choice["remove"]


def _uninstall_offer_backup(app_root: Path) -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    wants_backup = messagebox.askyesno(
        "Panga - Back up your data?",
        (
            "Back up your data before it's removed?\n\n"
            "This saves a copy of data/ and config/ to a zip file you choose.\n"
            "Note: this zip alone can't be read without your recovery code\n"
            "(Settings > Data Recovery, or see docs/encryption-at-rest.md) -\n"
            "the recovery code is the real fallback, not the zip by itself."
        ),
        parent=root,
    )
    if not wants_backup:
        root.destroy()
        return

    dest = filedialog.asksaveasfilename(
        title="Save Panga data backup",
        defaultextension=".zip",
        filetypes=[("Zip archive", "*.zip")],
        initialfile="panga-data-backup.zip",
        parent=root,
    )
    if dest:
        try:
            _write_backup_zip(app_root, Path(dest))
            messagebox.showinfo("Panga", f"Backup saved to:\n{dest}", parent=root)
        except Exception as e:  # noqa: BLE001 - never let a backup failure block uninstall
            messagebox.showerror("Panga", f"Backup failed, continuing uninstall:\n{e}", parent=root)
    root.destroy()


def _write_backup_zip(app_root: Path, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for sub in ("data", "config"):
            src_dir = app_root / sub
            if not src_dir.exists():
                continue
            for path in src_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(app_root))


def _uninstall_offer_license_release() -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    wants_release = messagebox.askyesno(
        "Panga - Release this device's license?",
        (
            "Release this device's Panga license now, so you can activate\n"
            "it on another computer later?\n\n"
            "Safe to skip if you're staying on this computer, or plan to\n"
            "reinstall here."
        ),
        parent=root,
    )
    if wants_release:
        try:
            _add_dev_paths()
            from licensing.client import is_configured, release_device

            if is_configured():
                release_device(via="uninstall")
        except Exception:  # noqa: BLE001 - best-effort only, per scope doc: offline or a
            # revoked refresh token just means this silently doesn't happen
            # and the customer falls back to the manual lost-device flow.
            # ~3s timeout is enforced inside release_device() itself
            # (licensing/client.py's _UNINSTALL_TIMEOUT).
            messagebox.showinfo(
                "Panga",
                "Couldn't reach the license server (offline, or already released).\n"
                "You can deactivate this device manually later, or contact support.",
                parent=root,
            )
    root.destroy()


def _uninstall_remove_data(app_root: Path) -> None:
    import shutil

    # data/security/ and the OS keyring entry come after the license call
    # (see _uninstall_offer_license_release, called before this) - that
    # call needs data/security/license_state.json's cached refresh token
    # to still exist. See docs/native-packaging-scope.md, "License device
    # release" ordering note.
    security_dir = app_root / "data" / "security"
    if security_dir.exists():
        shutil.rmtree(security_dir, ignore_errors=True)

    try:
        import keyring

        from security.crypto_store import _KEY_USERNAME, _SERVICE_NAME  # noqa: PLC2701 - reusing the
        # existing constants rather than re-declaring "Panga"/the key
        # username a second time; crypto_store.py itself is untouched.
        keyring.delete_password(_SERVICE_NAME, _KEY_USERNAME)
    except Exception:  # noqa: BLE001 - key may already be gone; never block uninstall
        pass

    data_dir = app_root / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)

    settings_path = app_root / "config" / "settings.yaml"
    settings_path.unlink(missing_ok=True)


def cmd_uninstall_helper() -> int:
    _add_dev_paths()
    app_root = _app_root()
    try:
        wants_remove = _uninstall_ask_data_retention()
        if wants_remove:
            _uninstall_offer_backup(app_root)
        _uninstall_offer_license_release()
        if wants_remove:
            _uninstall_remove_data(app_root)
    except Exception as e:  # noqa: BLE001 - never let the helper block the uninstaller
        try:
            _error_box("Panga", f"Uninstall helper hit an unexpected error and is continuing anyway:\n{e}")
        except Exception:  # noqa: BLE001
            pass
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        cmd_gui()
        return 0
    if args[0] == "--serve":
        port = int(args[args.index("--port") + 1])
        cmd_serve(port)
        return 0
    if args[0] == "--task" and len(args) > 1:
        return cmd_task(args[1])
    if args[0] == "--uninstall-helper":
        return cmd_uninstall_helper()
    sys.stderr.write(f"Unknown arguments: {args}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
