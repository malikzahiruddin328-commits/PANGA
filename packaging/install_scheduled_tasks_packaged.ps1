# Packaged-install counterpart of scripts\install_scheduled_tasks.ps1
# (native-packaging Phase 1). That script targets venv\Scripts\python.exe
# + a scripts\*.py path, which don't exist in a PyInstaller build - there's
# no venv, and run_search.py/gmail_cta_scan.py/cta_fulfillment.py are
# bundled inside Panga.exe, invoked as `Panga.exe --task <name>` (see
# packaging/panga_launcher.py's cmd_task). Kept as a separate file rather
# than editing the original so the original stays a correct, working
# description of the dev-mode (venv) setup untouched by packaging.
#
# Same schedule as the original (and the Claude Code tasks before that -
# see docs/email-monitoring-task.md, docs/daily-job-search-task.md). Run
# this once per install, after .env has ANTHROPIC_API_KEY and
# data\gmail\credentials.json exists (see
# docs\native-packaging-task-scheduler.md in the source repo - shipped
# installs don't carry docs\, so read it before building if you need the
# full setup steps). No admin rights needed for per-user scheduled tasks.
# Safe to re-run - each task is registered with -Force.

$ErrorActionPreference = "Stop"

$AppDir = Split-Path -Parent $PSScriptRoot
$PangaExe = Join-Path $AppDir "Panga.exe"

function Register-PangaTask {
    param(
        [string]$Name,
        [string]$TaskArg,
        [array]$Triggers
    )
    $action = New-ScheduledTaskAction -Execute $PangaExe -Argument "--task $TaskArg" -WorkingDirectory $AppDir
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Triggers -Settings $settings -Force | Out-Null
    Write-Host "Registered '$Name'"
}

# panga-daily-job-search -> once daily, 7:26am
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At 7:26am
Register-PangaTask -Name "Panga-DailyJobSearch" -TaskArg "run-search" -Triggers @($dailyTrigger)

# panga-gmail-cta-scan -> 4x/day, 8:07am/12:07pm/4:07pm/8:07pm
$scanTriggers = @(
    New-ScheduledTaskTrigger -Daily -At 8:07am
    New-ScheduledTaskTrigger -Daily -At 12:07pm
    New-ScheduledTaskTrigger -Daily -At 4:07pm
    New-ScheduledTaskTrigger -Daily -At 8:07pm
)
Register-PangaTask -Name "Panga-GmailCtaScan" -TaskArg "gmail-scan" -Triggers $scanTriggers

# panga-cta-fulfillment -> every 10 minutes. Task Scheduler rejects
# [TimeSpan]::MaxValue as a repetition duration, so this uses 10 years as a
# practical stand-in, same as the original script - re-run this (safe,
# uses -Force) to extend it before that expires.
$fulfillmentTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-PangaTask -Name "Panga-CtaFulfillment" -TaskArg "cta-fulfillment" -Triggers @($fulfillmentTrigger)

Write-Host ""
Write-Host "All 3 tasks registered. View/manage them with:"
Write-Host "  Get-ScheduledTask -TaskName 'Panga-*'"
Write-Host "  Start-ScheduledTask -TaskName 'Panga-DailyJobSearch'   # run one on demand"
