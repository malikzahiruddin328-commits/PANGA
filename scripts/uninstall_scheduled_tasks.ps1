# Removes the Windows Task Scheduler tasks install_scheduled_tasks.ps1
# registers (native-packaging branch, 2026-07-31; Panga-JobAlertScan added
# 2026-08-07). Safe to run even if some or all of them were never
# registered - each removal is best-effort.
#
# -TaskPrefix must match whatever install_scheduled_tasks_packaged.ps1 was
# run with (default "Panga-") - see that script's own -TaskPrefix param.
# This is how a test build's install/uninstall cycle stays isolated from a
# real production install's tasks on the same machine.
param(
    [string]$TaskPrefix = "Panga-"
)

$TaskNames = @("${TaskPrefix}DailyJobSearch", "${TaskPrefix}GmailCtaScan", "${TaskPrefix}CtaFulfillment", "${TaskPrefix}JobAlertScan")

foreach ($name in $TaskNames) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed '$name'"
    } else {
        Write-Host "'$name' was not registered - nothing to remove"
    }
}
