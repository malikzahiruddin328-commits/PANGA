"""Standalone replacement for the panga-cta-fulfillment Claude scheduled
task (native-packaging branch, 2026-07-31; extended 2026-08-04 to fulfill
requests across every configured inbox account; extended again 2026-08-05
to share its actual logic with the dashboard's synchronous "Send and
receive" button) - ported step-for-step from
C:\\Users\\User\\.claude\\scheduled-tasks\\panga-cta-fulfillment\\SKILL.md
(see docs/email-monitoring-task.md).

This script is now a thin wrapper: all the archive/draft/reconcile logic
(CTA emails AND Prospector outreach, using inbox_accounts.py's per-provider
adapters and tailoring.cta_reasoning/prospector.outreach_reasoning for
reply/outreach composition) lives in src/fulfillment.py, so a manual sync
click and this 2x/day (8am/4pm - throttled down from every 10 minutes
2026-08-05 for cost reasons) scheduled run can never drift apart in
behavior - see that module's docstring for the full design.

Only ever creates DRAFTS, never sends - Zahir reviews and sends every
reply himself, matching the original SKILL.md's explicit safety property.
"""

import sys
from pathlib import Path

# See gmail_cta_scan.py's identical comment - real email subjects/senders
# contain arbitrary Unicode that a Windows console's default codepage can't
# encode; an unguarded print() would crash the whole run on one.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fulfillment import run_full_fulfillment  # noqa: E402
from notifications import send_notification  # noqa: E402


def _log(message: str) -> None:
    print(message, flush=True)


def run() -> None:
    _log("Running fulfillment (archive/draft/reconcile - CTA + outreach)...")
    summary = run_full_fulfillment()
    _log(
        f"Done - {summary['archived']} archived, {summary['cta_drafts']} CTA draft(s), "
        f"{summary['outreach_drafts']} outreach draft(s), {summary['failures']} failure(s)."
    )
    if summary["failures"]:
        send_notification(
            "Panga - CTA fulfillment issue",
            f"{summary['failures']} archive/draft action(s) failed this run - check the app.",
        )


if __name__ == "__main__":
    run()
