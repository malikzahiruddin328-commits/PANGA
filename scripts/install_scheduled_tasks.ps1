# *** NOT ACTIVE TODAY. Do not treat `schtasks`/Task Scheduler entries this
# *** script created as evidence Panga's real automation is running or
# *** broken - check `mcp__scheduled-tasks__list_scheduled_tasks` (or
# *** ~/.claude/scheduled-tasks/) instead, that's the live system. ***
# Real incident, 2026-08-11: an audit checked Task Scheduler, found
# Panga-JobAlertScan never registered and Panga-CtaFulfillment Disabled/
# stale, and reported live automation as broken. It wasn't - the actual
# live automation (Claude Code's own scheduled-tasks system) was running
# fine the whole time; Task Scheduler here was just never fully wired up
# for a native-packaged build that hasn't shipped yet. See
# docs/manual-sync-button-scope.md's 2026-08-11 correction for the full
# story - don't repeat this confusion.
#
# Registers Panga's background jobs as native Windows Task Scheduler
# tasks (native-packaging branch, 2026-07-31; Panga-JobAlertScan added
# 2026-08-07) - this is scaffolding for a FUTURE standalone-build install,
# meant to replace the Claude Code scheduled tasks (panga-daily-job-search,
# panga-gmail-cta-scan, panga-cta-fulfillment) that a packaged build can't
# carry (no live Claude Code session to run them in). Same schedule as the
# live originals (see docs/email-monitoring-task.md,
# docs/daily-job-search-task.md).
#
# Run this once from an elevated-or-not PowerShell (no admin rights needed
# for per-user scheduled tasks) after Phase 1's direct-API scripts are
# configured (.env has ANTHROPIC_API_KEY, data/gmail/credentials.json
# exists). Safe to re-run - each task is registered with -Force, so a
# second run just updates the existing definitions rather than erroring or
# duplicating them.
#
# Tasks run only while this Windows account is logged on (not "whether
# user is logged on or not") - src/notifications.py's balloon-tip
# notifications need an interactive desktop session to display, same real
# constraint the original Claude Code tasks had ("only run while the
# Claude app is open").

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"

function Register-PangaTask {
    param(
        [string]$Name,
        [string]$ScriptRelativePath,
        [array]$Triggers
    )
    $scriptPath = Join-Path $ProjectRoot $ScriptRelativePath
    $action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$scriptPath`"" -WorkingDirectory $ProjectRoot
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Triggers -Settings $settings -Force | Out-Null
    Write-Host "Registered '$Name'"
}

# panga-job-alert-scan -> once daily, 7:00am - runs shortly before
# Panga-DailyJobSearch (7:26am) so listings this scan saves (which get no
# fit_score of their own - see job_alert_scan.py's docstring) are already
# in the store by the time that task's score_unscored_jobs() sweep runs,
# rather than waiting a full extra day for the sweep after it. 1x/day
# (not 4x/day like the CTA scan) per Zahir's explicit cost-conscious call
# (relayed via General, 2026-08-07) - a new listing isn't time-critical
# the way a CTA reply is, so this starts at the conservative end and can
# be retuned later if daily turns out to miss anything in practice.
$jobAlertTrigger = New-ScheduledTaskTrigger -Daily -At 7:00am
Register-PangaTask -Name "Panga-JobAlertScan" -ScriptRelativePath "scripts\job_alert_scan.py" -Triggers @($jobAlertTrigger)

# panga-daily-job-search -> once daily, 7:26am (same time as the original)
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At 7:26am
Register-PangaTask -Name "Panga-DailyJobSearch" -ScriptRelativePath "scripts\run_search.py" -Triggers @($dailyTrigger)

# panga-gmail-cta-scan -> 4x/day, 8:07am/12:07pm/4:07pm/8:07pm (cron
# "7 8,12,16,20 * * *" from the original SKILL.md)
$scanTriggers = @(
    New-ScheduledTaskTrigger -Daily -At 8:07am
    New-ScheduledTaskTrigger -Daily -At 12:07pm
    New-ScheduledTaskTrigger -Daily -At 4:07pm
    New-ScheduledTaskTrigger -Daily -At 8:07pm
)
Register-PangaTask -Name "Panga-GmailCtaScan" -ScriptRelativePath "scripts\gmail_cta_scan.py" -Triggers $scanTriggers

# panga-cta-fulfillment -> every 10 minutes, indefinitely (cron
# "*/10 * * * *" from the original SKILL.md). Task Scheduler rejects
# [TimeSpan]::MaxValue as a repetition duration ("value ... out of range") -
# there's no real "forever" value for RepetitionDuration, so this uses 10
# years as a practical stand-in; re-run this script (safe, uses -Force) to
# extend it before that expires.
$fulfillmentTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-PangaTask -Name "Panga-CtaFulfillment" -ScriptRelativePath "scripts\cta_fulfillment.py" -Triggers @($fulfillmentTrigger)

Write-Host ""
Write-Host "All 4 tasks registered. View/manage them with:"
Write-Host "  Get-ScheduledTask -TaskName 'Panga-*'"
Write-Host "  Start-ScheduledTask -TaskName 'Panga-DailyJobSearch'   # run one on demand"
Write-Host "  .\scripts\uninstall_scheduled_tasks.ps1                # remove all 4"
