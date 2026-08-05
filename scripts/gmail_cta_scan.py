"""Standalone replacement for the panga-gmail-cta-scan Claude scheduled
task (native-packaging branch, 2026-07-31; extended 2026-08-04 to scan
every configured inbox account, not just Gmail) - ported step-for-step
from C:\\Users\\User\\.claude\\scheduled-tasks\\panga-gmail-cta-scan\\SKILL.md
(see docs/email-monitoring-task.md), using inbox_accounts.py's per-provider
adapters (gmail_client.py/microsoft_client.py/imap_client.py - no MCP
anywhere, see that module's docstring) in place of the MCP connector, and
tailoring.cta_reasoning (direct Anthropic API) in place of live Claude
Code reasoning for classification and application matching. Kept this
filename despite no longer being Gmail-only - Windows Task Scheduler
references it by path (see docs/native-packaging-task-scheduler.md), and
renaming would silently break an already-registered task until someone
re-runs the install script.

Safety property preserved from the original: this script only ever reads
and labels inboxes, and only ever *suggests* an application status change
(suggest_status - Zahir still confirms it himself in the Streamlit app). It
never sends, replies to, or drafts a reply to anything - that only happens
in cta_fulfillment.py, and only in response to Zahir clicking "Draft reply"
himself.

Sequential across accounts, not concurrent - see inbox_accounts.py's
module docstring for why (avoids introducing any new concurrency on top
of the shared stores' existing file locking). One account's failure (a
revoked OAuth token, an unreachable IMAP server) is logged and skipped,
not fatal to the rest of the run - see run()'s per-account try/except.
"""

import sys
from pathlib import Path

# Real emails contain arbitrary Unicode (emoji, accented names, etc.) - a
# Windows console's default codepage (cp1252) can't encode most of it, so
# an unguarded print() on a real subject/sender crashes the whole run.
# Scheduled Task Scheduler runs have no console attached at all, where this
# matters even more (stdout is redirected, encoding still applies).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inbox_accounts import configured_accounts  # noqa: E402
from notifications import send_notification  # noqa: E402
from tailoring.applications import load_applications, suggest_status  # noqa: E402
from tailoring.cta_emails import add_cta_email  # noqa: E402
from tailoring.cta_reasoning import classify_thread, match_application_confirmation  # noqa: E402


def _log(message: str) -> None:
    print(message, flush=True)


def scan_account(account) -> tuple[list[dict], int]:
    """Runs the classify/store/match loop for one configured account.
    Returns (new_cta_items, new_match_count)."""
    messages = account.list_recent_unreviewed()
    _log(f"  [{account.provider}:{account.account}] {len(messages)} new message(s) to classify")

    new_cta_items = []
    new_match_count = 0

    for msg in messages:
        thread_summary = {"subject": msg.subject, "sender": msg.sender, "date": msg.date, "snippet": msg.snippet}
        try:
            result = classify_thread(thread_summary)
            if not result["confident"]:
                result = classify_thread(thread_summary, full_body=account.get_body(msg.ref))
        except Exception as exc:  # noqa: BLE001 - one message's failure shouldn't stop the rest
            _log(f"    classification failed for {msg.subject!r}: {exc}")
            continue

        bucket = result["bucket"]
        if bucket == "not_related":
            continue

        if bucket == "passive":
            try:
                account.mark_reviewed(msg.ref)
            except Exception as exc:  # noqa: BLE001 - best-effort marking (e.g. an IMAP
                # server that rejects custom keyword flags)
                _log(f"    couldn't mark reviewed for {msg.subject!r}: {exc}")
            continue

        if bucket == "call_to_action":
            try:
                account.mark_cta(msg.ref)
            except Exception as exc:  # noqa: BLE001
                _log(f"    couldn't mark CTA for {msg.subject!r}: {exc}")
            add_cta_email(
                msg.ref, msg.subject, msg.sender, msg.snippet, msg.date,
                result["cta_category"], message_id=msg.message_id,
                provider=account.provider, account=account.account,
                web_link=account.web_link(msg.ref),
            )
            new_cta_items.append({"subject": msg.subject, "sender": msg.sender, "category": result["cta_category"]})
            continue

        if bucket == "application_confirmation":
            try:
                account.mark_reviewed(msg.ref)
            except Exception as exc:  # noqa: BLE001
                _log(f"    couldn't mark reviewed for {msg.subject!r}: {exc}")
            under_review = [a for a in load_applications() if a.get("status") == "under review"]
            if not under_review:
                continue
            try:
                body = account.get_body(msg.ref)
                match = match_application_confirmation(thread_summary, body, under_review)
            except Exception as exc:  # noqa: BLE001
                _log(f"    application match failed for {msg.subject!r}: {exc}")
                continue
            if match["matched"]:
                suggest_status(match["source"], match["job_id"], "applied", match["reason"])
                new_match_count += 1

    return new_cta_items, new_match_count


def run() -> None:
    accounts = configured_accounts()
    if not accounts:
        _log("No email accounts configured - nothing to scan.")
        return

    all_new_cta_items: list[dict] = []
    total_new_match_count = 0
    for account in accounts:
        try:
            new_cta_items, new_match_count = scan_account(account)
        except Exception as exc:  # noqa: BLE001 - one account's failure (expired OAuth
            # token, unreachable IMAP server) must not stop the others from scanning
            _log(f"  [{account.provider}:{account.account}] scan failed: {exc}")
            continue
        all_new_cta_items.extend(new_cta_items)
        total_new_match_count += new_match_count

    _notify(all_new_cta_items, total_new_match_count)
    _log("Done.")


def _notify(new_cta_items: list[dict], new_match_count: int) -> None:
    parts = []
    if new_cta_items:
        listed = "; ".join(f"{item['category']} from {item['sender']}" for item in new_cta_items[:3])
        remainder = len(new_cta_items) - 3
        if remainder > 0:
            listed += f", +{remainder} more"
        parts.append(f"{len(new_cta_items)} job repl{'ies' if len(new_cta_items) != 1 else 'y'} need you: {listed}")
    if new_match_count:
        parts.append(f"{new_match_count} application match{'es' if new_match_count != 1 else ''} ready to confirm")
    if not parts:
        return
    send_notification("Panga - Inbox scan", ". Also: ".join(parts)[:200])


if __name__ == "__main__":
    run()
