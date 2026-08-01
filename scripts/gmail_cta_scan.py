"""Standalone replacement for the panga-gmail-cta-scan Claude scheduled
task (native-packaging branch, 2026-07-31) - ported step-for-step from
C:\\Users\\User\\.claude\\scheduled-tasks\\panga-gmail-cta-scan\\SKILL.md
(see docs/email-monitoring-task.md), using gmail_client.py (official Gmail
API) in place of the MCP connector and tailoring.cta_reasoning (direct
Anthropic API) in place of live Claude Code reasoning for classification
and application matching.

Safety property preserved from the original: this script only ever reads
and labels Gmail, and only ever *suggests* an application status change
(suggest_status - Zahir still confirms it himself in the Streamlit app). It
never sends, replies to, or drafts a reply to anything - that only happens
in cta_fulfillment.py, and only in response to Zahir clicking "Draft reply"
himself.
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

import gmail_client  # noqa: E402
from notifications import send_notification  # noqa: E402
from tailoring.applications import load_applications, suggest_status  # noqa: E402
from tailoring.cta_emails import add_cta_email  # noqa: E402
from tailoring.cta_reasoning import classify_thread, match_application_confirmation  # noqa: E402

SEARCH_QUERY = "-label:Panga/Reviewed -in:spam -in:trash newer_than:2d in:inbox"


def _log(message: str) -> None:
    print(message, flush=True)


def _full_body(thread_id: str) -> str:
    thread = gmail_client.get_thread(thread_id)
    return "\n\n".join(m["body"] for m in thread["messages"] if m["body"])


def run() -> None:
    reviewed_label_id = gmail_client.ensure_label("Panga/Reviewed")
    cta_label_id = gmail_client.ensure_label("Panga/Call-to-Action")

    threads = gmail_client.search_threads(SEARCH_QUERY)
    _log(f"Found {len(threads)} new thread(s) to classify")

    new_cta_items = []
    new_match_count = 0

    for thread in threads:
        try:
            result = classify_thread(thread)
            if not result["confident"]:
                result = classify_thread(thread, full_body=_full_body(thread["thread_id"]))
        except Exception as exc:  # noqa: BLE001 - one thread's failure shouldn't stop the rest
            _log(f"  classification failed for {thread['subject']!r}: {exc}")
            continue

        bucket = result["bucket"]
        if bucket == "not_related":
            continue

        if bucket == "passive":
            gmail_client.label_thread(thread["thread_id"], [reviewed_label_id])
            continue

        if bucket == "call_to_action":
            gmail_client.label_thread(thread["thread_id"], [reviewed_label_id, cta_label_id])
            add_cta_email(
                thread["thread_id"], thread["subject"], thread["sender"], thread["snippet"], thread["date"],
                result["cta_category"], message_id=thread.get("message_id"),
            )
            new_cta_items.append({**thread, "category": result["cta_category"]})
            continue

        if bucket == "application_confirmation":
            gmail_client.label_thread(thread["thread_id"], [reviewed_label_id])
            under_review = [a for a in load_applications() if a.get("status") == "under review"]
            if not under_review:
                continue
            try:
                body = _full_body(thread["thread_id"])
                match = match_application_confirmation(thread, body, under_review)
            except Exception as exc:  # noqa: BLE001
                _log(f"  application match failed for {thread['subject']!r}: {exc}")
                continue
            if match["matched"]:
                suggest_status(match["source"], match["job_id"], "applied", match["reason"])
                new_match_count += 1

    _notify(new_cta_items, new_match_count)
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
    send_notification("Panga - Gmail scan", ". Also: ".join(parts)[:200])


if __name__ == "__main__":
    run()
