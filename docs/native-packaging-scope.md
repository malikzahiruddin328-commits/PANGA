# Native Windows Packaging — scope

Branch: `feature/native-packaging`. Started 2026-07-31, split out of the
"General" brainstorming session into its own dedicated chat per Zahir's
usual one-session-per-purpose pattern. Full backlog context:
`docs/job-search-automation-prd.md` §13 — "Native Windows app packaging"
and "Direct LLM API integration (replace Claude Code orchestration)" rows.

## Goal

Zahir wants to eventually **sell** this app, not just run it locally — so
"packaging" here means a real standalone Windows installer someone else
could run on their own machine, not just a shortcut for Zahir's own dev box.

## Why this has two phases, in this order

A `.exe` you hand to a stranger can't carry a live Claude Code session with
it. Right now Panga's reasoning (fit scoring, drafting, CTA classification,
Prospector Score, LinkedIn analysis, interview prep) and its Gmail/
ZipRecruiter/Dice access all work *only* because the app runs inside a live
Claude Code session — that's what the MCP connectors and the 3 scheduled
tasks depend on. Building the installer before solving this would produce
something that opens to a broken/nonfunctional app for anyone who isn't
Zahir in his current dev environment. So:

**Phase 1 — direct-API prerequisite (do first, it's the part that actually
blocks a working standalone build):**
- Own Anthropic API key + direct API calls for every reasoning step
  currently done live in-conversation (not just document drafting, which
  already got a direct-API exception — see `tailoring/drafting.py`). Get
  real per-feature cost numbers before committing to this widely.
- Gmail's official API in place of the MCP connector (OAuth flow the
  installed app can run itself, no Claude Code needed).
- Windows Task Scheduler in place of Claude Code's scheduled-task system,
  for the 3 existing scheduled jobs (`panga-daily-job-search`,
  `panga-gmail-cta-scan`, `panga-cta-fulfillment`).
- Research whether ZipRecruiter/Dice expose anything reachable outside the
  MCP connector; if not, that source may need to degrade gracefully or
  drop out of the standalone build.

**Phase 2 — actual packaging (cheap once Phase 1 works):**
- Bundle the Python/Streamlit codebase with **PyInstaller** into a
  standalone executable — no separate Python install needed on the target
  machine.
- Wrap the Streamlit UI in a **pywebview** window instead of opening a
  browser tab, so it reads as a real desktop app.
- Build a proper installer (**Inno Setup**): Start Menu + Desktop
  shortcuts, uninstaller entry in Add/Remove Programs.

**Uninstall behavior — designed 2026-08-01, real risk for a paying
customer if done naively:**
- **Default uninstall never touches user data.** The uninstaller removes
  only app binaries/shortcuts. `data/` (encrypted resume, jobs,
  applications, target accounts, CTA emails, and the Learn Engine's
  accumulated decision/outcome history — the "insights it's learned from
  outcomes," not a separate trained-model file) and `config/settings.yaml`
  are left alone by default, as is the AES key in Windows Credential
  Manager (which lives **outside** `data/` — if that gets scrubbed while
  the data files survive, the data becomes permanently unreadable even
  though it's still on disk).
- **The uninstaller asks explicitly, in plain language**, e.g.: *"Panga
  also stores your job search data on this computer (resume, job matches,
  application history, and the insights it's learned from your outcomes).
  Keep this data, or remove it too?"* — "keep" is the default/recommended
  choice, not "remove."
- **If the customer chooses to remove it, offer a backup first** — export
  `data/` + `config/` to a zip at a location they choose, and remind them
  their recovery code (already built — `scripts/recover_access.py`,
  `docs/encryption-at-rest.md` §Recovery) is the real fallback, since a
  backup zip alone is useless without the encryption key.
- **License device release**: uninstall should also offer to call the
  licensing branch's self-service "deactivate this device" endpoint (see
  `feature/licensing`'s `docs/licensing-scope.md`), so a legitimate
  uninstall-and-move doesn't force the customer through the manual
  lost-device support flow. If offline at uninstall time, fail gracefully
  and tell them to deactivate manually later or contact support — don't
  block the uninstall on a network call.
  - **Ordering, confirmed with the Licensing session 2026-08-01**: call
    device-release BEFORE deleting `data/security/` or removing the OS
    keyring entry, not after — cleanup last, network-dependent step
    first. Licensing's `client.py` already transparently refreshes an
    expired access token as long as the cached refresh token/credential
    file is still present and hasn't itself been revoked, so this doesn't
    need a fresh/live login at uninstall time, just the credential file to
    still exist when the call is made. Stays best-effort either way
    (~3s timeout, swallow all failures, never block uninstall) — offline
    or a revoked refresh token just means the release silently doesn't
    happen and the customer falls back to the manual lost-device flow,
    same as today.
- No language change — Python's native-packaging tooling (PyInstaller/
  Nuitka) is mature enough; a C#/Java/Electron rewrite would cost all the
  existing scoring/search/encryption logic for no real gain.

## Phase 2 status (2026-08-03)

Started. All packaging code lives under `packaging/` - deliberately
additive, no changes to `src/`, `scripts/`, or `requirements.txt`. See
`docs/native-packaging-phase2-build.md` for the build sequence and what's
been verified vs. not.

- `packaging/panga_launcher.py` - the one PyInstaller entry point, four
  modes by argv (GUI, `--serve`, `--task`, `--uninstall-helper`). See its
  own module docstring for why GUI mode runs the Streamlit server as a
  child process rather than a background thread (a real constraint hit
  while building this, not a style choice - `bootstrap.run()` registers a
  SIGTERM handler that only works on an actual main thread).
- `packaging/panga.spec` - PyInstaller spec, **`--onedir`, not
  `--onefile`** - this is the artifact-shape answer
  feature/update-mechanism needs (see "Explicitly out of scope" below):
  a stable directory (`Panga.exe` + `_internal\`), not a single file that
  re-extracts to a fresh temp path on every launch.
- `packaging/panga.iss` - Inno Setup script. Per-user install
  (`{localappdata}\Programs\Panga`, no admin/UAC), fixed AppId GUID
  `29D8F805-F3A8-455D-AC29-482DDED84C50` (keep this exact value in every
  future release - Inno uses it, not AppName, to recognize upgrades).
  Start Menu + optional Desktop shortcut; Add/Remove Programs uninstall
  entry comes from Inno automatically, no extra config needed.
- **Artifact layout** - the reason zero `src/`/`scripts/` changes were
  needed: PyInstaller's default `--onedir` layout puts collected pure-Python
  modules under `_internal\` at the same relative nesting depth their
  source files had under `src\` (`_internal\tailoring\dossier.py` vs.
  `src\tailoring\dossier.py` - same depth, `_internal\` structurally
  standing in for `src\`). Every module's own
  `PROJECT_ROOT = Path(__file__).resolve().parents[N]` therefore still
  resolves to the exe's own directory when frozen, same as it resolves to
  the repo root in dev mode - confirmed by building a real bundle from
  this repo's actual `security/crypto_store.py`, `gmail_client.py`, and
  `tailoring/dossier.py` and printing where each landed. `config/` and
  `.streamlit/` are **not** part of the PyInstaller bundle itself (its own
  `datas` mechanism would nest them under `_internal\`, one level too
  deep) - the installer copies them in as real top-level `{app}\config`,
  `{app}\.streamlit` instead.
- Uninstaller: `panga_launcher.py --uninstall-helper`, run by
  `panga.iss`'s `[Code]` section at Inno's `usUninstall` step (before file
  removal) - implements the data-retention prompt / backup offer / license
  device-release ordering fixed above, using tkinter for the dialogs (same
  toolkit `scripts/recover_access.py` already uses for its own standalone
  dialog, not a new dependency).
- Task Scheduler: `packaging/install_scheduled_tasks_packaged.ps1` is a
  separate file from Phase 1's `scripts/install_scheduled_tasks.ps1`, not
  an edit to it - the original targets `venv\Scripts\python.exe`, which a
  packaged install doesn't have; the packaged one targets
  `Panga.exe --task <name>` instead. `scripts/uninstall_scheduled_tasks.ps1`
  is shipped and used unmodified (purely name-based, no python-path
  assumption, works against either setup).

## Explicitly out of scope for this branch

- **Update/hotfix delivery** — separate branch/session
  (`feature/update-mechanism`, see `docs/update-mechanism-scope.md`).
  Coordinate before merge: the updater needs to know the exact artifact
  shape this branch produces (single-file vs. directory PyInstaller build).
  **Answer, as of the "Phase 2 status" section above**: directory build
  (`--onedir`) - `Panga.exe` + `_internal\` under a stable per-user install
  directory (`{localappdata}\Programs\Panga` by default), not a
  self-extracting single file. Also needs the fixed Inno Setup AppId GUID
  (`29D8F805-F3A8-455D-AC29-482DDED84C50`) if the updater ever drives Inno
  Setup's own upgrade path rather than replacing files itself, and should
  know `data\security\license_state.json` (the licensing branch's cached
  refresh token) needs to survive an in-place update undisturbed - flagged
  independently in `licensing/local_state.py`'s own docstring as "not yet
  confirmed with the update-mechanism branch".
- **Licensing / per-user API key handling for a sold product** — separate,
  not-yet-designed backlog item (PRD §13), deliberately deferred until this
  branch and its direct-API prerequisite are further along, since license/
  billing design depends on how end-user API calls actually get made.
- **Code signing** — not needed yet (Zahir is still the only real user).
  Updated 2026-08-01 per MS Store research from the General session: if
  distribution ever goes through the Microsoft Store, or a direct-download
  link surfaced *via* the Store, code signing becomes a **hard
  requirement**, not deferred polish — a real certificate purchase (cost
  line item), not something to route around silently. Revisit as a real
  budget item once Store distribution is actually scoped, not just SmartScreen
  friction on an unsigned installer.
- **Microsoft Store Generative-AI content policy (11.16)** — flagged
  2026-08-01, not urgent yet: Store listing requirements include (1)
  disclosing that Panga uses Claude/Anthropic AI, and (2) giving users a
  way to report bad AI output that's actually acted on. Panga's existing
  point-and-talk feedback widget (`ui/feedback_widget.py`, on every tab) is
  a plausible reuse for (2) — worth reusing rather than building a separate
  reporting mechanism, when Store distribution is actually scoped.

## A note on shared files

`src/ui/app.py` and similar shared files have a history of being touched
by multiple concurrent sessions at once (see `[[project-panga-job-search-tool]]`
memory, "Concurrent sessions" entry). Check `git diff` before committing
here — don't sweep up another branch's in-progress changes.
