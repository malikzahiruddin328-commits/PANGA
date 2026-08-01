# Native Windows Task Scheduler wiring (Phase 1, native-packaging branch)

Replaces the 3 Claude Code scheduled tasks (`panga-daily-job-search`,
`panga-gmail-cta-scan`, `panga-cta-fulfillment` - see
`docs/daily-job-search-task.md`, `docs/email-monitoring-task.md`) with
native Windows Task Scheduler entries running the standalone scripts under
`scripts/`, so a standalone build needs no live Claude Code session at all.

| Original Claude task | Standalone script | Schedule | Windows task name |
|---|---|---|---|
| `panga-daily-job-search` | `scripts/run_search.py` | daily, 7:26am | `Panga-DailyJobSearch` |
| `panga-gmail-cta-scan` | `scripts/gmail_cta_scan.py` | 4x/day, 8:07/12:07/16:07/20:07 | `Panga-GmailCtaScan` |
| `panga-cta-fulfillment` | `scripts/cta_fulfillment.py` | every 10 minutes | `Panga-CtaFulfillment` |

## Setup

1. `ANTHROPIC_API_KEY` in `.env` (same as the rest of the app).
2. `data/gmail/credentials.json` - Zahir's own Gmail OAuth client (Desktop
   app type), from Google Cloud Console. See `src/gmail_client.py`'s
   `get_credentials()` docstring for the exact steps.
3. Run `scripts/gmail_cta_scan.py` once by hand first - this is when the
   one-time OAuth consent browser popup happens and `data/gmail/token.json`
   gets cached; letting a scheduled (non-interactive-friendly, but still
   needs a logged-in session) run be the *first* run would otherwise open a
   browser window nobody's watching for.
4. `powershell -File scripts\install_scheduled_tasks.ps1` - registers all 3
   tasks. Re-runnable safely (each is registered with `-Force`).
5. `powershell -File scripts\uninstall_scheduled_tasks.ps1` to remove them.

## Real limits to keep in mind

- Tasks run only while this Windows account is logged on (not "whether
  user is logged on or not") - `src/notifications.py`'s balloon-tip
  notifications need an interactive desktop session, the same real
  constraint the original Claude Code tasks had (needing the Claude app
  open).
- `install_scheduled_tasks.ps1` points at whatever checkout it's run from
  (`$PSScriptRoot`-relative) - re-run it after moving/reinstalling the app
  to a new location (e.g. after Phase 2's installer lands), or the
  registered tasks will point at a stale path.
- Not yet run against this machine's real Task Scheduler from this branch
  (deliberately - see below).

## Why this wasn't registered live from this worktree

This branch's checkout lives under
`Panga\.claude\worktrees\native-packaging\` - a temporary worktree path,
not `Panga`'s real location. Registering real `Panga-*` Task Scheduler
entries pointing at that path now would leave stale/wrong entries the
moment this branch merges and the worktree is cleaned up. The install
script itself was validated (PowerShell-parsed clean, and a throwaway
`Panga-SmokeTest-DoNotKeep` task was registered and torn down successfully
to confirm `Register-ScheduledTask`/`Unregister-ScheduledTask` work as
written) - actually running `install_scheduled_tasks.ps1` for real should
happen once this branch is merged to master and running from Panga's real
location, as part of turning off the old Claude scheduled tasks.
