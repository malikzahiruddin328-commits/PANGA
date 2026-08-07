# Removes the Windows Task Scheduler tasks install_scheduled_tasks.ps1
# registers (native-packaging branch, 2026-07-31; Panga-JobAlertScan added
# 2026-08-07). Safe to run even if some or all of them were never
# registered - each removal is best-effort.

$TaskNames = @("Panga-DailyJobSearch", "Panga-GmailCtaScan", "Panga-CtaFulfillment", "Panga-JobAlertScan")

foreach ($name in $TaskNames) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed '$name'"
    } else {
        Write-Host "'$name' was not registered - nothing to remove"
    }
}
