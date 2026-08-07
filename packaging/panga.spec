# PyInstaller spec for Panga's standalone desktop build (native-packaging
# Phase 2 - see docs/native-packaging-scope.md). --onedir, not --onefile:
# a onefile build re-extracts itself to a temp dir on every launch, which
# is slower and, worse, changes on every run - Streamlit's own file-watcher
# and any code doing __file__-relative path math (which is most of src/,
# see docs/native-packaging-scope.md "Artifact layout") would be resolving
# against a fresh throwaway directory each time instead of a stable
# install location. --onedir keeps one stable directory, which is also the
# artifact shape feature/update-mechanism needs to know about (a directory
# tree to diff/replace, not a single file) - see that scope doc's
# coordination note.
#
# Build with:  pyinstaller packaging/panga.spec
# (run from the repo root, with a venv that has requirements.txt AND
# packaging/requirements-desktop.txt installed - see
# docs/native-packaging-phase2-build.md)

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

REPO_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is injected by PyInstaller


def _find_bhangi_src(repo_root):
    """Same sibling-checkout search as src/ui/app.py's _find_bhangi_src -
    kept in sync deliberately (see that function's docstring for why this
    walk exists). Needed at build time too: Bhangi's own modules have to be
    on the Analysis pathex for `from bhangi.ui import ...` to resolve into
    the frozen bundle, since a real standalone install has no sibling
    Bhangi checkout on disk to find at runtime the way a dev worktree does."""
    override = os.environ.get("BHANGI_PATH")
    if override:
        candidate = Path(override) / "src"
        if candidate.is_dir():
            return candidate
    for ancestor in (repo_root, *repo_root.parents):
        candidate = ancestor.parent / "Bhangi" / "src"
        if candidate.is_dir():
            return candidate
    return None

# Streamlit and pywebview both need their non-code data files (Streamlit's
# frontend static build; pywebview's EdgeChromium/WebView2 loader DLL and
# platform backends) - collect_all is the documented blanket fix for
# "works when run from source, breaks when frozen" bugs with both.
datas = []
binaries = []
hiddenimports = []
for pkg in ("streamlit", "webview"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# google-api-python-client's discovery docs and google_auth_oauthlib are
# loaded dynamically by name (src/gmail_client.py) - not always picked up
# by static analysis.
hiddenimports += [
    "googleapiclient.discovery",
    "google_auth_oauthlib.flow",
    "google.auth.transport.requests",
    "keyring.backends.Windows",
]

# panga_launcher.py's cmd_task() reaches these via importlib.import_module()
# with a runtime string (see packaging/panga_launcher.py's _TASKS dict) -
# static analysis can't trace a dynamic import, so they'd silently be left
# out of the bundle without this. Confirmed by building without this block
# first: none of the 3 names showed up in Analysis-00.toc at all.
hiddenimports += [
    "run_search",
    "gmail_cta_scan",
    "cta_fulfillment",
]
# job_alert_scan (feature/job-alert-scan, merging to master separately -
# flagged by the hub 2026-08-06) isn't in this worktree yet. Conditional so
# this spec doesn't break today's build but picks it up automatically once
# scripts/job_alert_scan.py lands here via a merge from master - same
# importlib.import_module() blind spot as the 3 above, see
# packaging/panga_launcher.py's cmd_task()/_TASKS dict.
if (REPO_ROOT / "scripts" / "job_alert_scan.py").exists():
    hiddenimports += ["job_alert_scan"]

# cmd_serve() reaches ui/app.py by file path (Streamlit's bootstrap.run()
# needs an actual script path, not an import), so nothing in
# panga_launcher.py ever `import`s it - Analysis's static entry-point walk
# never discovers it or anything it imports (tailoring/, search/,
# prospector/, linkedin/, security/, gmail_client, profile/, licensing/,
# bhangi.ui - see src/ui/app.py's own import list). Confirmed empirically:
# building without this line produced a frozen exe that returned HTTP 200
# from --serve (Streamlit's own server came up fine) but the page itself
# was Streamlit's "Script execution error" boundary, not the real app -
# a 200 status alone doesn't prove app.py loaded. hiddenimports here makes
# Analysis trace the whole graph so it's actually bundled, but app.py also
# needs to exist as a literal file (see datas below) since bootstrap.run()
# opens it directly rather than importing it.
hiddenimports += ["ui.app"]

bhangi_src = _find_bhangi_src(REPO_ROOT)
bhangi_pathex = [str(bhangi_src)] if bhangi_src else []
if bhangi_src:
    hiddenimports += ["bhangi.ui"]
# else: leave bhangi.ui out of hiddenimports - Analysis would fail
# immediately on an unresolvable import. The resulting bundle's Support tab
# won't work (matches today's dev-mode behavior when no sibling Bhangi
# checkout exists), but the rest of the app still builds and runs.

# The literal ui/app.py source file, at the exact path _bundle_dir()/"ui"/
# "app.py" resolves to when frozen (_bundle_dir() returns sys._MEIPASS,
# i.e. _internal/ - see panga_launcher.py) - hiddenimports above puts a
# compiled copy in the PYZ archive for `import` purposes, but
# bootstrap.run() needs a real file on disk to open and exec.
datas += [(str(REPO_ROOT / "src" / "ui" / "app.py"), "ui")]

a = Analysis(
    [str(REPO_ROOT / "packaging" / "panga_launcher.py")],
    pathex=[str(REPO_ROOT / "src"), str(REPO_ROOT / "scripts")] + bhangi_pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Panga",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed - this is a desktop app, not a CLI tool
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # No icon yet (none exists in the repo - see docs/native-packaging-scope.md's
    # code-signing note, same "not yet, revisit before real distribution" bucket).
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Panga",
)
