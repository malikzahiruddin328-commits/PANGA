@echo off
rem Bundles everything useful for diagnosing a friend-tester's session into
rem one zip: their per-screen "Leave feedback on this screen" notes
rem (data\ui_feedback), the in-app debug log (data\logs\panga_debug.log,
rem only exists if PANGA_DEBUG=1 was set - see run_app_friend_test.bat),
rem and the raw Streamlit console output (streamlit_console.log). Never
rem includes their resume/profile/job data - only feedback and logs, so
rem it's safe to email back without exposing personal application data.
rem
rem Stages copies in a temp folder before zipping rather than zipping the
rem live files directly - the log files are usually still open (Panga
rem running in the background, the normal case when someone hits an issue
rem and wants to export right away without closing the app first), and
rem Compress-Archive fails outright on a file another process has open.
rem Copy-Item can read a shared-open file even when Compress-Archive can't.
cd /d "%~dp0"

rem The timestamp used to be built in pure batch via %DATE:~4,2%-style fixed-
rem offset slicing, which assumes one specific Windows regional date format
rem (MM/DD/YYYY) - on any other locale it silently produces a garbled
rem string (e.g. "2026-8"), which then broke Compress-Archive's output path
rem below (found live 2026-08-04, a friend tester's own machine). Since
rem this script already shells out to PowerShell for everything else, let
rem Get-Date -Format build the timestamp instead - deterministic regardless
rem of regional settings, no batch date-parsing involved at all.
echo Collecting feedback and logs...
powershell -NoProfile -Command ^
  "$stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'; " ^
  "$outzip = Join-Path (Get-Location) ('Panga-Feedback-' + $stamp + '.zip'); " ^
  "$stage = Join-Path $env:TEMP ('panga_feedback_stage_' + [guid]::NewGuid().ToString('N')); " ^
  "New-Item -ItemType Directory -Path $stage | Out-Null; " ^
  "$found = $false; " ^
  "foreach ($item in @('data\ui_feedback', 'data\logs', 'streamlit_console.log')) { " ^
  "  if (Test-Path $item) { " ^
  "    try { Copy-Item $item -Destination $stage -Recurse -Force -ErrorAction Stop; $found = $true } " ^
  "    catch { Write-Output ('Skipped ' + $item + ' - could not read it right now.') } " ^
  "  } " ^
  "}; " ^
  "if (-not $found) { Write-Output 'Nothing to collect yet - use the app a bit first, then run this again.'; Remove-Item $stage -Recurse -Force; exit 1 }; " ^
  "try { Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $outzip -Force -ErrorAction Stop } " ^
  "catch { Write-Output ('Could not create the zip: ' + $_.Exception.Message); Remove-Item $stage -Recurse -Force; exit 1 }; " ^
  "Remove-Item $stage -Recurse -Force; " ^
  "Write-Output ('Created ' + $outzip)"

rem A wildcard exist-check, not a re-derived exact filename - the timestamp
rem is built once, inside the PowerShell block above, and never duplicated
rem in batch, so there's nothing here that could disagree with it.
if exist "Panga-Feedback-*.zip" (
    echo.
    echo Done. Send the newest "Panga-Feedback-*.zip" file in this folder back -
    echo it only contains your typed feedback notes and diagnostic logs, not
    echo your resume or job data.
    echo.
) else (
    echo.
    echo Something went wrong creating the zip - see the message above for why.
    echo.
)
pause
