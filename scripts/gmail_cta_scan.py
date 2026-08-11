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

Status matching (2026-08-06): a classified rejection/interview_request/
offer email is also matched against every non-terminal application and,
if confidently matched, suggests the corresponding status
(_CTA_STATUS_BY_CATEGORY below) - closing a real gap Mirror's PRD-vs-code
audit found (the backlog log had marked this Done since 2026-07-30, but the
call_to_action bucket only ever stored the email, never called
suggest_status; the only real matching that existed was the older
"applied" match on confirmation emails below). assessment_request/
recruiter_question are deliberately excluded - neither has a
corresponding status value in applications.py's lifecycle.

Sequential across accounts, not concurrent - see inbox_accounts.py's
module docstring for why (avoids introducing any new concurrency on top
of the shared stores' existing file locking). One account's failure (a
revoked OAuth token, an unreachable IMAP server) is logged and skipped,
not fatal to the rest of the run - see run()'s per-account try/except.

Candidate-job join (2026-08-07): real gap flagged by this script's own
scheduled run report and relayed back by General - applications.json
records only carry source/job_id/status (see applications.py's
docstring), no title/organization, so handing them straight to
match_cta_application()/match_application_confirmation() as "candidate
jobs" gave the LLM nothing but an id and a status to compare an email's
actual content against; it could never have matched anything for real.
_candidates_with_job_details() below joins each candidate application
against search.job_store.load_jobs() (by source+job_id) to attach the
title/organization/location the match calls actually need. The old
docs/email-monitoring-task.md SKILL.md this script replaced never
documented this join either - not fixed there since that file is a
historical reference for a retired MCP-based design, not something that
still executes; this script (and this docstring) is the live behavior
now. jobs_by_key is built once per run() call, not once per email -
search.job_store.load_jobs() is a 1600+-record store, and this script
can check several emails per run (see CLAUDE.md's "avoid O(n^2) scans
over growing stores").

Mark-reviewed-on-every-terminal-path (2026-08-09): real bug found by
Backlog against the live code, not a hypothetical - two separate gaps,
both fixed here:
1. classify_thread() raising skipped any mark-reviewed call entirely, so
   a persistently-unparseable message retried (a real AI call) forever.
   Now uses scan_retry_tracker: a failure only gets marked reviewed
   (given up on) after MAX_ATTEMPTS consecutive failures, not the first
   one - a single transient blip still gets retried next run, same as
   before, but a message that will never succeed eventually stops
   costing real money.
2. bucket == "not_related" - a clean, SUCCESSFUL classification, no
   error at all - simply never called mark_reviewed(), unlike its
   sibling "passive" bucket right below it which always did. This was
   the common case, not an edge case: most inbox traffic classifies as
   not_related, and every one of those messages was being reclassified
   (again, a real AI call) on every single run. Fixed to mark reviewed
   the same way "passive" already does.
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
from scan_retry_tracker import MAX_ATTEMPTS, clear_failure, record_failure  # noqa: E402
from search.job_store import load_jobs  # noqa: E402
from tailoring.applications import load_applications, suggest_status  # noqa: E402
from tailoring.cta_emails import add_cta_email  # noqa: E402
from tailoring.cta_reasoning import classify_thread, match_application_confirmation, match_cta_application  # noqa: E402

_SCAN_NAME = "gmail_cta_scan"

# Which cta_category values represent a real application-status
# transition, and what status to suggest for each - only these 3 of the 5
# categories (see tailoring/cta_reasoning.py's classify_thread docstring
# for the full set) have a corresponding value in applications.py's
# status lifecycle. assessment_request and recruiter_question are
# deliberately excluded: neither one is a status change (an assessment
# ask is still mid-"applied", and a recruiter question often isn't tied
# to an existing application at all), so matching one against the
# applications list would either misrepresent the real stage or just
# waste an API call on emails with nothing to match.
_CTA_STATUS_BY_CATEGORY = {
    "rejection": "rejected",
    "interview_request": "interview scheduled",
    "offer": "offer",
}

# Applications already in one of these are excluded from CTA matching -
# a rejection/interview/offer email about an application that's already
# closed out has nothing left to update (see applications.py's status
# lifecycle docstring for the full set of values).
_TERMINAL_APPLICATION_STATUSES = {"rejected", "not interested", "save for later", "closed by employer"}


def _log(message: str) -> None:
    print(message, flush=True)


def _candidates_with_job_details(applications: list[dict], jobs_by_key: dict) -> list[dict]:
    """Joins each application record against its job record (by
    source+job_id) so the match calls have title/organization/location to
    actually compare an email against - see this file's module docstring
    for why this join exists. An application with no corresponding job
    record (shouldn't happen in practice, but not guaranteed) is skipped
    rather than passed through with blank fields - a candidate with no
    title/organization is exactly the "nothing to compare against" case
    this fix removes, so silently including one would reintroduce it."""
    candidates = []
    for app in applications:
        job = jobs_by_key.get((app.get("source"), app.get("job_id")))
        if not job:
            continue
        candidates.append({
            "source": app["source"],
            "job_id": app["job_id"],
            "status": app.get("status"),
            "title": job.get("title", ""),
            "organization": job.get("organization", ""),
            "location": job.get("location", ""),
        })
    return candidates


def scan_account(account, jobs_by_key: dict) -> tuple[list[dict], int]:
    """Runs the classify/store/match loop for one configured account.
    jobs_by_key is a {(source, job_id): job_record} index, built once per
    run() call and passed down - see this file's module docstring for why
    it isn't rebuilt per-message. Returns (new_cta_items, new_match_count)."""
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
            failures = record_failure(_SCAN_NAME, account.provider, account.account, msg.ref)
            if failures >= MAX_ATTEMPTS:
                _log(f"    classification failed for {msg.subject!r} {failures} times - giving up, marking reviewed: {exc}")
                try:
                    account.mark_reviewed(msg.ref)
                except Exception as mark_exc:  # noqa: BLE001
                    _log(f"    couldn't mark reviewed after giving up on {msg.subject!r}: {mark_exc}")
            else:
                _log(f"    classification failed for {msg.subject!r} (attempt {failures}/{MAX_ATTEMPTS}): {exc}")
            continue
        else:
            clear_failure(_SCAN_NAME, account.provider, account.account, msg.ref)

        bucket = result["bucket"]
        if bucket == "not_related":
            try:
                account.mark_reviewed(msg.ref)
            except Exception as exc:  # noqa: BLE001 - best-effort marking (e.g. an IMAP
                # server that rejects custom keyword flags)
                _log(f"    couldn't mark reviewed for {msg.subject!r}: {exc}")
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
            category = result["cta_category"]
            add_cta_email(
                msg.ref, msg.subject, msg.sender, msg.snippet, msg.date,
                category, message_id=msg.message_id,
                provider=account.provider, account=account.account,
                web_link=account.web_link(msg.ref),
            )
            new_cta_items.append({"subject": msg.subject, "sender": msg.sender, "category": category})

            target_status = _CTA_STATUS_BY_CATEGORY.get(category)
            if target_status:
                open_applications = [a for a in load_applications() if a.get("status") not in _TERMINAL_APPLICATION_STATUSES]
                candidates = _candidates_with_job_details(open_applications, jobs_by_key)
                if candidates:
                    try:
                        body = account.get_body(msg.ref)
                        match = match_cta_application(category, thread_summary, body, candidates)
                    except Exception as exc:  # noqa: BLE001 - a failed match shouldn't drop the
                        # CTA email itself, which is already stored above
                        _log(f"    CTA application match failed for {msg.subject!r}: {exc}")
                    else:
                        if match["matched"]:
                            suggest_status(match["source"], match["job_id"], target_status, match["reason"])
                            new_match_count += 1
            continue

        if bucket == "application_confirmation":
            try:
                account.mark_reviewed(msg.ref)
            except Exception as exc:  # noqa: BLE001
                _log(f"    couldn't mark reviewed for {msg.subject!r}: {exc}")
            under_review = [a for a in load_applications() if a.get("status") == "under review"]
            candidates = _candidates_with_job_details(under_review, jobs_by_key)
            if not candidates:
                continue
            try:
                body = account.get_body(msg.ref)
                match = match_application_confirmation(thread_summary, body, candidates)
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

    # Built once for the whole run, not once per email - see this file's
    # module docstring on the candidate-job join.
    jobs_by_key = {(j.get("source"), j.get("job_id")): j for j in load_jobs()}

    all_new_cta_items: list[dict] = []
    total_new_match_count = 0
    for account in accounts:
        try:
            new_cta_items, new_match_count = scan_account(account, jobs_by_key)
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
