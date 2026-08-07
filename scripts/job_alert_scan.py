"""Automated replacement for the manual "read LinkedIn/Lensa digest
emails, add each posting via add_manual_job() by hand" process CLAUDE.md
used to describe under "Processing job-alert emails into job records"
(now updated to point here) - built 2026-08-07 per Zahir's explicit ask
(relayed via General): a real scheduled scan, not a live session reading
his inbox, both for cost (a manual session doing this regularly is itself
expensive, separate from any per-listing AI cost) and consistency.

Structured exactly like scripts/gmail_cta_scan.py: loops
inbox_accounts.configured_accounts() sequentially (same reasoning as that
script - no new concurrency on top of security/file_lock.py's existing
protection), per-account and per-message try/except so one failure never
kills the run. Two real differences from that script:

1. Scope is a user-configurable allowlist (src/search/job_alert_senders.py,
   editable from Settings), not a live classification of every inbox
   message - Zahir's explicit ask was a defined sender/domain allowlist,
   not a generic "looks like a job listing" heuristic, to avoid false
   positives. Each account's list_job_alert_candidates(senders) already
   does the sender filtering (see inbox_accounts.py); this script never
   inspects a message from outside the configured list.
2. "Reviewed" tracking uses its own dedicated label/keyword
   (inbox_accounts.JOB_ALERT_LABEL/IMAP_JOB_ALERT_KEYWORD), never the CTA
   scan's REVIEWED_LABEL - see inbox_accounts.py's module comment above
   those constants for why sharing one would silently starve this scan.

Extraction (tailoring.job_alert_reasoning.extract_listings) never skips a
listing for looking like the wrong industry/vertical - same standing rule
CLAUDE.md already states for the old manual process, unchanged here.
Listings save via search.job_store.add_manual_job(), so job_id dedup
(LinkedIn URL job-id regex, else a URL hash) and the source tag are
exactly the manual "Add a job manually" flow's own logic - not
reimplemented here. Newly-saved jobs get no fit_score at save time; the
existing scripts/run_search.py's daily score_unscored_jobs() sweep
(scoped to ALL jobs missing fit_score store-wide, not just its own
freshly-searched ones) picks them up automatically on its next run - see
install_scheduled_tasks.ps1's comment on this script's registered time
for why it's scheduled just before that sweep rather than needing its own
scoring step.

A listing with no posting_url is skipped and logged, not saved: job_store
add_manual_job()'s dedup falls back to hashing posting_url when no
LinkedIn /jobs/view/<id> pattern matches, and hashing "" would collide
every no-URL listing onto the same job_id, silently dropping all but the
first. In practice this should be rare - a job-alert digest without a
link to the actual posting isn't useful to Zahir either.
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inbox_accounts import configured_accounts  # noqa: E402
from notifications import send_notification  # noqa: E402
from search.job_alert_senders import load_job_alert_senders  # noqa: E402
from search.job_store import add_manual_job  # noqa: E402
from tailoring.job_alert_reasoning import extract_listings  # noqa: E402


def _log(message: str) -> None:
    print(message, flush=True)


def scan_account(account, senders: list[str]) -> list[dict]:
    """Runs the fetch/extract/save loop for one configured account against
    the configured sender allowlist. Returns the list of genuinely new
    job records added (already deduped by add_manual_job -> save_jobs)."""
    messages = account.list_job_alert_candidates(senders)
    _log(f"  [{account.provider}:{account.account}] {len(messages)} job-alert message(s) to process")

    new_jobs = []

    for msg in messages:
        try:
            body = account.get_body(msg.ref)
            listings = extract_listings(msg.subject, body)
        except Exception as exc:  # noqa: BLE001 - one message's failure shouldn't stop the rest
            _log(f"    extraction failed for {msg.subject!r}: {exc}")
            continue

        for listing in listings:
            if not listing.get("posting_url"):
                _log(f"    skipped a listing with no posting_url ({listing.get('title')!r}) - can't reliably dedupe or link to it")
                continue
            try:
                job = add_manual_job(
                    title=listing["title"],
                    organization=listing["organization"],
                    location=listing["location"],
                    description=listing["description"],
                    posting_url=listing["posting_url"],
                    source=senders_source_for(msg.sender, senders),
                )
            except Exception as exc:  # noqa: BLE001 - one bad listing shouldn't stop the rest
                _log(f"    couldn't save listing {listing.get('title')!r}: {exc}")
                continue
            new_jobs.append(job)

        try:
            account.mark_job_alert_reviewed(msg.ref)
        except Exception as exc:  # noqa: BLE001 - best-effort marking (e.g. an IMAP
            # server that rejects custom keyword flags)
            _log(f"    couldn't mark job-alert-reviewed for {msg.subject!r}: {exc}")

    return new_jobs


def senders_source_for(sender_header: str, senders: list[dict]) -> str:
    """Maps a message's From header to the job_store `source` tag its
    matching allowlist entry specifies (e.g. "linkedin", "lensa") - falls
    back to "job alert" if somehow no configured entry's sender string
    appears in the header (shouldn't happen, since the message was only
    fetched because it matched one), so a save never hard-fails on this."""
    lowered = (sender_header or "").lower()
    for entry in senders:
        if entry.get("sender", "").lower() in lowered:
            return entry.get("source") or "job alert"
    return "job alert"


def run() -> None:
    sender_entries = load_job_alert_senders()
    if not sender_entries:
        _log("No job-alert senders configured - nothing to scan.")
        return
    senders = [entry["sender"] for entry in sender_entries if entry.get("sender")]

    accounts = configured_accounts()
    if not accounts:
        _log("No email accounts configured - nothing to scan.")
        return

    all_new_jobs: list[dict] = []
    for account in accounts:
        try:
            new_jobs = scan_account(account, senders)
        except Exception as exc:  # noqa: BLE001 - one account's failure (expired OAuth
            # token, unreachable IMAP server) must not stop the others from scanning
            _log(f"  [{account.provider}:{account.account}] scan failed: {exc}")
            continue
        all_new_jobs.extend(new_jobs)

    _notify(all_new_jobs)
    _log("Done.")


def _notify(new_jobs: list[dict]) -> None:
    if not new_jobs:
        return
    listed = "; ".join(f"{j['title']} at {j['organization'] or 'unknown employer'}" for j in new_jobs[:3])
    remainder = len(new_jobs) - 3
    if remainder > 0:
        listed += f", +{remainder} more"
    send_notification("Panga - Job alerts", f"{len(new_jobs)} new listing{'s' if len(new_jobs) != 1 else ''} added: {listed}"[:200])


if __name__ == "__main__":
    run()
