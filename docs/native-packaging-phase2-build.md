# Native Windows Packaging — Phase 2 build steps

Companion to `docs/native-packaging-scope.md`. That doc has the design and
the "why"; this one is the literal command sequence to produce a real
`Panga-Setup.exe` installer from this checkout. Written 2026-08-03,
verified end-to-end (PyInstaller build + real `src/` module imports +
`ui/app.py` actually serving HTTP 200 from inside the frozen bundle) in a
throwaway venv — Inno Setup itself was **not** available in that
environment, so the `.iss` compile step below is written correctly per
Inno Setup 6's documented syntax but not run for real. Compile it once on
a machine with Inno Setup 6 installed before trusting the final
`Panga-Setup.exe` output; everything upstream of that (the PyInstaller
bundle, path resolution, `--serve`/`--task` modes) is real-tested.

## Prerequisites

- A Windows machine (not this worktree's dev sandbox) with:
  - Python (the same version this project's `venv/` normally uses)
  - [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed (gives you
    `ISCC.exe`, usually at `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`)

## 1. Build the PyInstaller bundle

From the repo root (not `packaging/`):

```
python -m venv build_venv
build_venv\Scripts\pip install -r requirements.txt -r packaging\requirements-desktop.txt
build_venv\Scripts\python -m PyInstaller -y packaging\panga.spec --distpath build_dist --workpath build_work
```

This produces `build_dist\Panga\Panga.exe` + `build_dist\Panga\_internal\`.
`build_venv/`, `build_dist/`, and `build_work/` are all gitignored - never
commit them.

**Sanity check before moving on** - run it and confirm it actually opens:

```
build_dist\Panga\Panga.exe
```

(No `config\`/`.streamlit\` next to the exe yet at this point - that's
normal, the `.iss` script adds those as part of the installer, not
PyInstaller. `Panga.exe` on its own will show Streamlit's own defaults for
theme, and app.py's config-loading code will hit its own not-found
handling.)

## 2. Compile the installer

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\panga.iss
```

Produces `installer_output\Panga-Setup.exe` (also gitignored). Run it on a
clean-ish machine/VM to confirm: Start Menu shortcut, optional Desktop
shortcut, launches to a working app window (not a browser tab), and
`config\`/`.streamlit\` land as real folders next to `Panga.exe` (not
nested under `_internal\`).

## 3. Uninstall test

Uninstall via Add/Remove Programs (or `{app}\unins000.exe` directly) after
having generated at least one real job/application record and a recovery
code (Settings > Data Recovery), so there's something real to click
"keep" vs "remove" on:

- Confirm the data-retention prompt shows before any files disappear, and
  that choosing "Keep my data" leaves `data\` and `config\settings.yaml`
  in `{app}` (Inno only removes the binaries/shortcuts it installed, not
  files the app itself created since - if you removed `data\` from the
  `[Files]` section correctly, Inno's own uninstall never touches it
  regardless of the prompt's answer; the prompt matters for the "remove"
  path).
- Confirm choosing "Remove my data too" actually deletes `data\` and
  `config\settings.yaml`, and that `keyring`'s Windows Credential Manager
  entry for `Panga`/`data-encryption-key` is gone (Control Panel >
  Credential Manager, or `cmdkey /list`).
- Confirm the 3 `Panga-*` Task Scheduler entries are gone
  (`Get-ScheduledTask -TaskName 'Panga-*'` should return nothing).

## What was verified in this sandbox (no Windows GUI, no Inno Setup)

- `python -m PyInstaller packaging/panga.spec` completes cleanly against
  this repo's real `requirements.txt` + `packaging/requirements-desktop.txt`
  (streamlit, pandas, cryptography, google-api-python-client, keyring,
  pywebview, etc. all resolved and bundled without errors).
- Every `src/` module's `PROJECT_ROOT = Path(__file__).resolve().parents[N]`
  pattern resolves to the frozen exe's own directory, unmodified - checked
  directly against `security/crypto_store.py`, `gmail_client.py`, and
  `tailoring/dossier.py` (the three different nesting depths used across
  `src/`) inside a real built bundle.
- `run_search.py`/`gmail_cta_scan.py`/`cta_fulfillment.py` (the Task
  Scheduler scripts) import cleanly inside the frozen bundle too - this
  needed an explicit `hiddenimports` fix in `panga.spec` first, since
  `panga_launcher.py`'s `cmd_task()` reaches them via a runtime
  `importlib.import_module()` call that PyInstaller's static analysis
  can't trace on its own (confirmed missing, then confirmed present after
  the fix, by grepping `Analysis-00.toc`).
- `Panga.exe --serve --port N` actually starts the real `ui/app.py` (the
  whole app, not a stand-in) and serves `HTTP 200` from inside the frozen
  bundle.

## What was NOT verified (needs a real Windows box with Inno Setup)

- The `.iss` script actually compiles (syntax is correct per Inno Setup
  6's documented grammar, but ISCC itself never ran against it).
- The pywebview GUI window itself (`cmd_gui()`) - needs a real desktop
  session and the Windows WebView2 runtime; the underlying subprocess +
  polling logic was validated (`--serve` mode boots and answers HTTP), but
  `webview.create_window()`/`webview.start()` were not exercised.
- The uninstall helper's tkinter dialogs and the `[Code]` section's
  `CurUninstallStepChanged` ordering - logic reviewed against Inno Setup's
  documented uninstall-step semantics, not run for real.
- `install_scheduled_tasks_packaged.ps1` against a real Task Scheduler
  (parses cleanly - checked with `PSParser.Tokenize` - same level of
  verification Phase 1's original script got before its own real-machine
  run, see docs/native-packaging-task-scheduler.md).
