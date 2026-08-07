@echo off
cd /d "%~dp0"

rem Dedicated port 8510, deliberately different from Claude Code's dev-
rem preview default (8501, see .claude\launch.json) - the 2026-07-31
rem ImportError Zahir hit was this shortcut's browser tab talking to a
rem STALE Claude-dev-preview process still squatting on 8501 from an
rem earlier session, holding old cached code, instead of a fresh process
rem reading the current file on disk. Separate ports means this app's own
rem launches never collide with (or get shadowed by) a dev-preview server.
rem Also kill anything already on 8510 first, in case a previous run of
rem THIS shortcut was left running/hung - guarantees every double-click
rem starts a genuinely fresh process, not a reused stale one.
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8510" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

venv\Scripts\streamlit.exe run src\ui\app.py --server.port 8510 --server.headless false
