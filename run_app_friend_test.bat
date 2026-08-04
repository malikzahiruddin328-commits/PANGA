@echo off
rem Friend-testing launcher (2026-08-03) - separate from run_app.bat, which
rem is Zahir's own everyday shortcut against his already-set-up venv/.env.
rem This one is meant to ship inside a zip to friends who have nothing set
rem up yet: it bootstraps its own venv, its own .env, and its own local
rem data\ folder on first run, then launches with PANGA_TEST_MODE=1 so
rem testers don't need a real Panga subscription (see
rem src\ui\license_gate.py's PANGA_TEST_MODE check). Deliberately does not
rem touch the in-progress native-packaging/installer work - this is a
rem plain "run from source" package, not a built .exe.
rem
rem Uses goto-based branching throughout, not nested if/else(...) blocks -
rem batch's parenthesized-block parser is fragile with quotes/parens inside
rem it (hit a real "was unexpected at this time" parse failure here during
rem testing 2026-08-03), goto avoids that whole class of bug.
cd /d "%~dp0"

echo ============================================
echo  Panga setup - Step 1 of 5: Checking Python
echo ============================================
where python >nul 2>&1
if errorlevel 1 goto need_python
echo Python found.
goto have_python

:need_python
rem The official Python installer is bundled in installers\ (see
rem installers\README.txt for its checksum) rather than downloaded at run
rem time - most friend testers won't have Python, and a runtime download
rem depends on their network being able to reach python.org, which isn't
rem a safe assumption for someone in another country on an unknown/
rem corporate/restricted connection. Bundling it means this step needs no
rem internet at all and always behaves the same way.
if not exist "installers\python-3.14.2-amd64.exe" goto python_installer_missing
echo.
echo Python was not found on this computer - installing it now, one time only,
echo from the copy included in this package. This may take a minute or two.
echo.
installers\python-3.14.2-amd64.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
echo.
echo Python is installed. Please close this window and double-click
echo run_app_friend_test.bat again to continue - this second run is needed
echo so the new Python can be found.
echo.
pause
exit /b 0

:python_installer_missing
echo.
echo Python isn't installed, and the bundled installer (installers\python-3.14.2-amd64.exe)
echo is missing from this copy of the folder. Please install Python yourself from
echo https://www.python.org/downloads/ - check the box "Add Python to PATH"
echo during install - then run this file again.
echo.
pause
exit /b 1

:have_python
echo.
echo ==================================================
echo  Panga setup - Step 2 of 5: Python environment
echo ==================================================
if exist venv goto venv_ready
echo Creating a private Python environment for Panga (one-time, roughly 10-20 seconds)...
python -m venv venv
if errorlevel 1 goto venv_failed
echo Done.
goto venv_ready

:venv_failed
echo.
echo Could not create the Python environment. Make sure Python installed correctly and try again.
pause
exit /b 1

:venv_ready
echo.
echo ==================================================
echo  Panga setup - Step 3 of 5: Installing packages
echo ==================================================
if exist venv\.deps_installed goto deps_ready
echo This is the longest step (roughly 2-5 minutes on a normal connection).
echo You'll see each package listed below as it downloads and installs - if
echo it pauses on one line for a while, that's normal, it's still working.
echo.
venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto deps_failed
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto deps_failed
echo done > venv\.deps_installed
echo.
echo All packages installed.
goto deps_ready

:deps_failed
echo.
echo Something went wrong installing packages. Check your internet connection and try again.
pause
exit /b 1

:deps_ready
echo.
echo ==================================================
echo  Panga setup - Step 4 of 5: Settings file
echo ==================================================
if exist .env goto env_ready
copy .env.example .env >nul
echo A blank settings file (.env) was created for you.
echo You can also add your API keys later from inside the app - go to the
echo Settings tab once it opens.
goto env_ready

:env_ready
echo Done.
echo.
echo ==================================================
echo  Panga setup - Step 5 of 5: Starting the app
echo ==================================================
rem Streamlit's own first-run "Welcome! enter your email" prompt reads from
rem the console and blocks everything until someone types something and
rem presses Enter - looks exactly like a frozen window if you don't know
rem to do that (found this the hard way testing 2026-08-03: an automated
rem background launch just hung forever on it). Pre-seeding an empty
rem credentials.toml is Streamlit's own documented way to skip that prompt
rem entirely, so the app just starts.
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    echo [general] > "%USERPROFILE%\.streamlit\credentials.toml"
    echo email = "" >> "%USERPROFILE%\.streamlit\credentials.toml"
)

set PANGA_TEST_MODE=1
set PANGA_DEBUG=1

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8520" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo Starting Panga (test mode - no subscription needed, debug logging on)...
echo A browser tab should open automatically in a few seconds.
echo.
echo IMPORTANT: if no browser tab opens, or you're not sure anything
echo happened, open your web browser yourself (Chrome, Edge, etc.) and go to
echo this address:
echo.
echo     http://localhost:8520
echo.
echo This window staying still with no new text IS NORMAL once the app has
echo started - it just means it's running and waiting, not stuck or broken.
echo Some technical-looking lines may appear below too (usually harmless) -
echo the app is only actually broken if the browser page itself won't load.
echo Close this window to stop the app.
echo If anything still looks wrong, run export_feedback_for_zahir.bat
echo afterwards and send the zip it creates - that's easier than describing
echo what happened.
echo.
rem Tee-Object keeps the console visible (so it doesn't look frozen) while
rem also writing everything to streamlit_console.log - the second half of
rem the debug picture alongside data\logs\panga_debug.log (debug_log.py's
rem in-app logging). Zahir's ask 2026-08-03: an easy-to-diagnose build for
rem a friend tester, without him needing to screen-share or transcribe a
rem console window that scrolls away.
rem --server.fileWatcherType none is required, not optional: this package
rem ships compiled .pyc instead of .py source (see the build step that
rem strips .py files after compileall), and Streamlit's hot-reload watcher
rem cannot read/hash a .pyc module's source to detect real changes -
rem discovered live 2026-08-03 when it treated every poll as "changed" and
rem reran the app in an infinite loop, never letting the page settle.
rem Friends never edit this code, so disabling the watcher costs nothing.
powershell -NoProfile -Command "& { venv\Scripts\streamlit.exe run src\ui\app.py --server.port 8520 --server.headless false --server.fileWatcherType none --logger.level=debug 2>&1 | Tee-Object -FilePath streamlit_console.log; (Get-Content streamlit_console.log -Raw) | Set-Content streamlit_console.log -Encoding utf8 }"
