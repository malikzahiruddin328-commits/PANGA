"""Native Windows notifications (native-packaging branch, 2026-07-31) -
replaces Claude Code's PushNotification tool, which the standalone scheduled
scripts this branch adds can no longer call (no live Claude Code session
backs them). Uses a plain Windows Forms balloon tip via PowerShell/.NET,
already present on every Windows install - no new dependency, and no
Start-Menu app registration needed (unlike a real toast notification, which
requires an AppUserModelID)."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def _ps_escape(text: str) -> str:
    """Escapes a string for embedding inside a PowerShell single-quoted
    literal - doubling embedded single quotes is the only escape needed in
    that context, and single-quoted strings never expand $variables or
    backticks, so this is safe even if a job title/organization name
    contains characters that would otherwise be interpreted."""
    return text.replace("'", "''")


def send_notification(title: str, message: str, timeout_seconds: int = 10) -> None:
    """Shows a system-tray balloon notification. Best-effort - swallows any
    failure (no notification backend available, running under a session
    with no desktop, etc.) rather than failing the caller's whole run over
    a notification that couldn't be shown."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        f"$n.ShowBalloonTip({timeout_seconds * 1000}, '{_ps_escape(title)}', "
        f"'{_ps_escape(message)}', [System.Windows.Forms.ToolTipIcon]::Info);"
        f"Start-Sleep -Seconds {timeout_seconds};"
        "$n.Dispose()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=timeout_seconds + 15,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        logger.exception("Failed to show system-tray notification: %r", title)
