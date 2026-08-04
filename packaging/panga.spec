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

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

REPO_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is injected by PyInstaller

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

a = Analysis(
    [str(REPO_ROOT / "packaging" / "panga_launcher.py")],
    pathex=[str(REPO_ROOT / "src"), str(REPO_ROOT / "scripts")],
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
