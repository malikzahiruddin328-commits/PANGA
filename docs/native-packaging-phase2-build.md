# Native Windows Packaging — Phase 2 build steps

Companion to `docs/native-packaging-scope.md`. That doc has the design and
the "why"; this one is the literal command sequence to produce a real
`Panga-Setup.exe` installer from this checkout. Written 2026-08-03 and
verified only in a throwaway venv with no Inno Setup available (a static
review, not a real run) — updated 2026-08-07 after running every step of
this doc for real for the first time, on a machine that already had a real
production Panga install on it. That last part matters: the original
2026-08-03 pass never caught it, but the packaged app hardcoded the same
Task Scheduler names and keyring service name as a real production
install. Testing the documented uninstall flow as originally written
would have deleted real scheduled tasks and the real data-encryption key.
See "Testing on a machine with a real install" below before running any
of this on a machine that isn't a clean VM.

Also caught by that real run: the 2026-08-03 "verified... `ui/app.py`
actually serving HTTP 200 from inside the frozen bundle" claim was a false
positive - Streamlit answers HTTP 200 from its own error-boundary page
too, and that's what was actually being served (`ui/app.py` was never
bundled at all; see step 1 below). A passing status code alone didn't
prove the app loaded. All three bugs found this way are now fixed; see
each step for what broke and how.

## Prerequisites

- A Windows machine (not this worktree's dev sandbox) with:
  - Python (the same version this project's `venv/` normally uses)
  - [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed (gives you
    `ISCC.exe`, usually at `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`)

## Testing on a machine with a real install

If the machine you're testing on also has a real production Panga install
(true for Zahir's own dev box - this is the normal case, not an edge
case), a test build must not use the real Task Scheduler names or the
real keyring entry. Both are isolated by default-preserving overrides:

- `install_scheduled_tasks_packaged.ps1` / `uninstall_scheduled_tasks.ps1`
  take a `-TaskPrefix` param (default `Panga-`, unchanged). Compile the
  test installer with an override so both the install shortcut and the
  automatic uninstall step use it:
  ```
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DTaskPrefix=PangaTest- packaging\panga.iss
  ```
- `src/security/crypto_store.py`'s keyring service name reads
  `PANGA_KEYRING_SERVICE` (default `Panga`, unchanged) - this is baked
  into the already-built PyInstaller bundle at *runtime*, not compile
  time, so `ISCC /D` can't reach it. Set it in the shell environment
  before launching anything that touches the test build (the installed
  exe, the uninstaller, or a script generating test data against it) -
  Windows child processes inherit it automatically down the chain
  (installer → app → uninstall helper):
  ```
  $env:PANGA_KEYRING_SERVICE = "PangaTest"
  ```

Verify isolation held *before* trusting any uninstall test result:
`Get-ScheduledTask -TaskName 'Panga-*'` should show the same real tasks
before and after; `keyring.get_password("Panga", "data-encryption-key")`
should be unchanged after a test "remove my data" run.

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

**2026-08-07: this sanity check alone isn't enough.** HTTP 200 just means
Streamlit's server came up - it doesn't prove `ui/app.py` itself loaded.
Nothing in `panga_launcher.py` ever `import`s `ui.app` (it's only
referenced by file path, for Streamlit's `bootstrap.run()`), so
PyInstaller's static analysis never walked its import graph - the app
wasn't bundled at all, and neither was anything it pulls in (`tailoring/`,
`search/`, `prospector/`, `linkedin/`, `security/`, `bhangi.ui`, etc). The
frozen exe served Streamlit's own "Script execution error" boundary page
at HTTP 200, which looked like success. Fixed in `panga.spec`: `"ui.app"`
added to `hiddenimports` (so Analysis traces its whole import graph, which
also required adding Bhangi's `src/` to `pathex` - a real standalone
install has no sibling Bhangi checkout to find at runtime the way a dev
worktree does) and the literal `ui/app.py` source file added to `datas`
(bootstrap.run() needs a real file to open, not just an importable
module). After the fix, load the page in a browser and check for actual
app content, not just the response code.

## 2. Compile the installer

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\panga.iss
```

(Add `/DTaskPrefix=PangaTest-` if the target machine has a real
production install - see "Testing on a machine with a real install"
above.)

**2026-08-07: this didn't actually compile as originally written** -
`AppId={#MyAppId}` expanded to a literal `{29D8F805-...}`, which Inno's
own compiler then tried to parse as a `{constant}` reference (unrelated
to ISPP's `{#...}` substitution syntax) and rejected as unknown. Fixed
with the standard Inno escape - the `#define` itself now starts with `{{`
so the substituted text reads as a literal `{`.

Produces `installer_output\Panga-Setup.exe` (also gitignored). Run it to
confirm: Start Menu shortcut, optional Desktop shortcut, launches to a
working app window (not a browser tab), and `config\`/`.streamlit\` land
as real folders next to `Panga.exe` (not nested under `_internal\`). All
confirmed 2026-08-07 - the GUI window is a real `WindowsForms10.Window`-
class window hosting WebView2, distinct from any browser process.

## 3. Uninstall test

Uninstall via Add/Remove Programs (or `{app}\unins000.exe` directly) after
having generated at least one real job/application record and a recovery
code (Settings > Data Recovery, or call
`security.crypto_store.generate_recovery_code()` /
`tailoring.applications.upsert_application()` directly against the
installed app's own `data\` dir if the license gate blocks reaching
Settings - see below), so there's something real to click "keep" vs
"remove" on:

- Confirm the data-retention prompt shows before any files disappear, and
  that choosing "Keep my data" leaves `data\` and `config\settings.yaml`
  in `{app}` (Inno only removes the binaries/shortcuts it installed, not
  files the app itself created since - if you removed `data\` from the
  `[Files]` section correctly, Inno's own uninstall never touches it
  regardless of the prompt's answer; the prompt matters for the "remove"
  path).
- Confirm choosing "Remove my data too" actually deletes `data\` and
  `config\settings.yaml`, and that `keyring`'s Windows Credential Manager
  entry is gone (Control Panel > Credential Manager, or `cmdkey /list`) -
  for the real service name if testing directly against production
  (careful!), or `PANGA_KEYRING_SERVICE` if testing isolated.
- Confirm the 3 (4, once `feature/job-alert-scan` merges here -
  `Panga-JobAlertScan`) Task Scheduler entries are gone
  (`Get-ScheduledTask -TaskName '<prefix>*'` should return nothing).

**2026-08-07: the uninstall helper crashed on every single run before any
of the above ever happened.** The data-retention dialog's `Label` widget
passed `pady=(20, 12)` as a widget *option* (which only accepts one
screen distance) instead of to `.pack()` (where a `(before, after)` tuple
is valid) - Tk stringified the tuple to `"20 12"` and its screen-distance
parser rejected it. `cmd_uninstall_helper()` wraps the whole flow in one
try/except "never let the helper block the uninstaller," so this crash on
the very first call was silently swallowed and reported only as a generic
error box - the data-retention prompt, backup offer, license release, and
conditional data removal never ran at all, for any uninstall, ever. Fixed
by moving the tuple to `.pack(pady=(20, 12))`.

License gate note: a fresh test install with no `.env` configured hits
`ui.license_gate`'s full-screen block before reaching Settings at all.
Getting past it for real needs the real `PANGA_LICENSE_SUPABASE_URL`/
`PANGA_LICENSE_SUPABASE_ANON_KEY` (safe to copy in - see `.env.example`'s
own comment, these are the public anon key, not a secret) plus a real
email-OTP round trip, which creates a real trial/device activation - ask
before doing that on someone else's account. The direct
`crypto_store.generate_recovery_code()` / `applications.upsert_application()`
route above avoids needing the license gate at all for uninstall testing
specifically (patch `PROJECT_ROOT`/`APPLICATIONS_PATH`/
`RECOVERY_ENVELOPE_PATH` on those modules to point at the installed test
app's own directory first).

## Verified end-to-end, 2026-08-07

Real machine, real Inno Setup 6, real Task Scheduler, isolated from the
real production install already on that machine (see "Testing on a
machine with a real install"):

- PyInstaller build completes; `ui/app.py` and everything it imports
  (including Bhangi) is genuinely bundled and runs, not just present at
  HTTP 200.
- `.iss` compiles; `Panga-Setup.exe` installs with the correct folder
  layout, working shortcuts, and a real (not browser-tab) GUI window.
- `install_scheduled_tasks_packaged.ps1` registers 3 real tasks against
  the real Task Scheduler with correct actions/triggers; the uninstall
  counterpart removes them.
- Both uninstall paths (keep data / remove data + backup offer + license
  release skip) run to completion against real encrypted test data.
- Throughout all of the above, the real production `Panga-*` Task
  Scheduler tasks and the real `Panga` keyring entry were confirmed
  unchanged.

Not yet exercised: the backup-zip file-save dialog itself (the "offer
backup" prompt was answered Yes, but the native save-file dialog didn't
get a chance to be clicked through interactively in that pass - worth a
follow-up if the backup path specifically needs sign-off), and
`Panga-JobAlertScan` (not yet merged into this worktree - see
`panga.spec`'s conditional `hiddenimports` entry for it).
