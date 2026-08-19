# Real launcher logic for run_app.bat (2026-08-18 reliability fix).
#
# Why this exists: the old run_app.bat found "whatever process owns port
# 8510 right now" via netstat/findstr and killed that, then started a new
# streamlit process without ever confirming the old one was actually gone
# or the new one actually came up. Real incident, 2026-08-18: a restart
# silently did not take effect - the OLD process (from hours earlier, pre-
# dating that night's code fixes) kept serving stale code the whole time,
# and it was only caught by manually checking the port's owning PID by
# hand. Zahir's own fix ("why don't you just save the PID to a file"):
# track the actual PID of the process THIS script started, in a file,
# so shutdown/restart always targets a specific known PID instead of
# re-discovering "whatever's on the port" every time - and verify the
# port is actually listening again before declaring success, instead of
# assuming a launch worked just because the command didn't error.
#
# The pidfile is overwritten every successful launch, so the next
# restart/shutdown always refers to the CURRENT run, never a stale one.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$pidFile = Join-Path $root "panga.pid"
$port = 8510

function Stop-TrackedProcess {
    if (-not (Test-Path $pidFile)) { return }
    $oldPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $oldPid) { return }
    $proc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Stopping previous Panga run (PID $oldPid)..."
        Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $oldPid -Timeout 15 -ErrorAction SilentlyContinue
    }
}

function Stop-AnyPortSquatter {
    # Safety net only, not the primary shutdown path - covers the case
    # where the pidfile is missing/stale (deleted, or a process was
    # started some other way) so this script never races a second
    # instance for the same port.
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        Write-Host "Stopping stray process on port $port (PID $($c.OwningProcess))..."
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

function Start-TrayMonitorIfNotRunning {
    # 2026-08-19 fix: the system-tray Task Monitor (src/task_tray.py,
    # merged 2026-08-19) was built, tested, and merged - but nothing ever
    # actually launched it, so it sat unused until manually started by
    # hand. Wiring it in here so every real production start/restart also
    # ensures the tray icon is running, without ever spawning a second one
    # on top of an already-running instance (checked by real command line,
    # not just "a python.exe exists" - matches this script's own
    # "verify the real state, don't assume" standard). Runs detached
    # (own process, not tracked by panga.pid) since the tray icon's own
    # lifetime is independent of the production app's restart cycle - it
    # should keep running (and briefly show "not running" via its own
    # poll loop) across a Panga restart, not be killed and relaunched
    # every time.
    $pythonExe = Join-Path $root "venv\Scripts\python.exe"
    $trayScript = Join-Path $root "src\task_tray.py"
    if (-not (Test-Path $trayScript)) { return }

    $existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains("task_tray.py") }
    if ($existing) {
        Write-Host "Tray monitor already running (PID $($existing[0].ProcessId))."
        return
    }

    try {
        Start-Process -FilePath $pythonExe -ArgumentList @($trayScript) -WorkingDirectory $root -WindowStyle Hidden | Out-Null
        Write-Host "Started tray monitor."
    } catch {
        # Never let a tray-monitor launch failure block or fail the real
        # app startup this script exists to make reliable - it's a
        # convenience layer on top of production, not a dependency of it.
        Write-Host "WARNING: could not start the tray monitor ($_) - Panga itself is unaffected."
    }
}

Stop-TrackedProcess
Stop-AnyPortSquatter
Start-Sleep -Milliseconds 500
Start-TrayMonitorIfNotRunning

$streamlitExe = Join-Path $root "venv\Scripts\streamlit.exe"
$appPath = Join-Path $root "src\ui\app.py"

$proc = Start-Process -FilePath $streamlitExe `
    -ArgumentList @("run", $appPath, "--server.port", "$port", "--server.headless", "false") `
    -WorkingDirectory $root -NoNewWindow -PassThru

$proc.Id | Out-File -Encoding ascii -FilePath $pidFile
Write-Host "Started Panga (PID $($proc.Id)), waiting for it to come up on port $port..."

$deadline = (Get-Date).AddSeconds(30)
$up = $false
while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) {
        Write-Host "ERROR: Panga process exited immediately (exit code $($proc.ExitCode)) - check the output above for a real startup error."
        Remove-Item $pidFile -ErrorAction SilentlyContinue
        exit 1
    }
    $listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listening) { $up = $true; break }
    Start-Sleep -Milliseconds 500
}

if ($up) {
    Write-Host "Panga is up: http://localhost:$port"
} else {
    Write-Host "WARNING: Panga did not start listening on port $port within 30s - it may still be starting, or something went wrong. Check the output above."
}

try {
    $proc.WaitForExit()
} finally {
    # Clean exit (window closed, Ctrl+C) - clear the pidfile so a stale
    # PID never lingers past its own process's real lifetime.
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}
