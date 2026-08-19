@echo off
cd /d "%~dp0"

rem Dedicated port 8510, deliberately different from Claude Code's dev-
rem preview default (8501, see .claude\launch.json) - the 2026-07-31
rem ImportError Zahir hit was this shortcut's browser tab talking to a
rem STALE Claude-dev-preview process still squatting on 8501 from an
rem earlier session, holding old cached code, instead of a fresh process
rem reading the current file on disk. Separate ports means this app's own
rem launches never collide with (or get shadowed by) a dev-preview server.
rem
rem Real logic lives in run_app.ps1 (2026-08-18 reliability fix): tracks
rem the actual PID of the process THIS script starts in panga.pid, so a
rem restart always targets a known process instead of re-discovering
rem "whatever's on the port" via netstat/findstr, and verifies the port
rem actually comes back up before declaring success. Real incident this
rem fixes: a restart that silently didn't take effect, leaving an hours-
rem old process serving stale code undetected until someone checked the
rem port's owning PID by hand.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_app.ps1"
