# Automated job-alert-email extraction — scope

Branch: `feature/job-alert-scan`. Started 2026-08-07, routed here by the
General hub session as email-scanning territory (Email Socket's domain).

## Why this exists

LinkedIn/Lensa job-alert digest emails were previously handled by a
Claude Code session manually reading Zahir's inbox and calling
`add_manual_job()` one at a time - no automated pipeline existed
(`CLAUDE.md`'s "Processing job-alert emails" section documented this as
explicitly not automated). Zahir's ask, confirmed via clarifying
questions before routing: replace this with a real scheduled scan, both
for cost (a manual session doing inbox-reading regularly is itself
expensive, separate from any per-listing AI cost) and consistency.

## Key design decisions

**A user-configurable allowlist, not a heuristic.** Zahir's explicit ask
was a defined list of senders/domains to scan (Settings tab, "Job-alert
email senders" - `src/search/job_alert_senders.py`), not a generic "looks
like a job listing" classifier over the whole inbox - avoids false
positives, mirrors `job_sources.py`'s already-merged "user-managed source
list, no code change needed" pattern (different data type - email
senders, not ATS company identifiers - so its own file, not a shared one).

**Independent "reviewed" tracking, not reused from the CTA scan.**
`gmail_cta_scan.py` already classifies bulk job-alert digests as
"passive" and marks them reviewed via its own shared `REVIEWED_LABEL` -
if this scan reused that same marker, the CTA scan (which runs 4x/day,
ahead of this scan's 1x/day) would starve it of every message before this
scan ever got a look. `inbox_accounts.py`'s new `JOB_ALERT_LABEL`/
`IMAP_JOB_ALERT_KEYWORD` constants are deliberately separate, so either
scan can process the same message without interfering with the other's
tracking.

**No new scoring logic.** `scripts/run_search.py`'s existing
`score_unscored_jobs()` sweep already scores every job missing a
`fit_score`, store-wide - not scoped to jobs its own search added. Jobs
this scan saves inherit that sweep automatically; `job_alert_scan.py`
needed no scoring code of its own. The new scheduled task is registered
at 7:00am, just ahead of `Panga-DailyJobSearch`'s 7:26am, so a listing
found today gets scored the same day rather than waiting until the sweep
after next.

**A listing with no `posting_url` is skipped, not saved.**
`job_store.add_manual_job()`'s dedup falls back to hashing `posting_url`
when no LinkedIn `/jobs/view/<id>` pattern matches - hashing an empty
string would collide every no-URL listing onto the same `job_id`, so
without this guard only the first ever such listing across all time would
be saved. In practice this should be rare - a job-alert digest without a
link to the actual posting isn't actionable for Zahir either.

**A thin/blank extraction is saved as-is, not backfilled or guessed at.**
`extract_listings()`'s system prompt explicitly instructs empty
`organization`/`description` over a guess. A job saved this way
automatically picks up the existing paste-JD-manually UX (`ui/app.py`'s
`render_paste_jd_prompt_before_drafting`, which already triggers off an
empty `job["description"]`) - no new fallback UI was needed, it's the
same code path every other thin job record (ZipRecruiter, Indeed, the
industry boards) already goes through. Checked real data before assuming
this gap's current shape: `data/jobs/jobs.json` currently has 33 LinkedIn
jobs, 0 with a blank organization - the gap Zahir remembered was already
hand-fixed, not present today; this design still guards against
reintroducing it.

**Frequency: 1x/day, not 4x/day like the CTA scan.** Explicit call from
General/Zahir - cost was the stated motivation for the whole feature, and
a new listing isn't time-critical the way a CTA reply is (someone waiting
on a reply vs. a posting simply existing a few hours later). Starting
conservative; easy to retune if daily turns out to miss anything in
practice.

## What was built

- `config/job_alert_senders.yaml` + `src/search/job_alert_senders.py` -
  load/save, YAML, `file_lock`-locked, same shape as `job_sources.py`.
- Settings UI section ("Job-alert email senders"), placed right after
  "Job-board sources" - same `st.data_editor(num_rows="dynamic")` pattern,
  no new UI idiom introduced.
- `src/tailoring/job_alert_reasoning.py` - `extract_listings(subject,
  body)`, direct-API, same `llm_client.call_structured` pattern as
  `cta_reasoning.py`. Returns a list (a digest can bundle several
  postings), each field `""` rather than guessed if not actually stated.
- `src/inbox_accounts.py` extended additively: `JOB_ALERT_LABEL`/
  `IMAP_JOB_ALERT_KEYWORD` constants, `list_job_alert_candidates(senders)`
  and `mark_job_alert_reviewed(ref)` on all three account classes.
  `list_recent_unreviewed()`/`REVIEWED_LABEL` (the CTA scan's own path)
  untouched.
- `scripts/job_alert_scan.py` - the scan script, structured like
  `gmail_cta_scan.py` (sequential across accounts, per-message/per-account
  try/except). Saves via the existing `add_manual_job()`, not new dedup
  logic.
- `Panga-JobAlertScan` registered in `install_scheduled_tasks.ps1`/
  `uninstall_scheduled_tasks.ps1`, daily 7:00am.
- `CLAUDE.md`'s "Processing job-alert emails" section updated to describe
  the automated pipeline instead of the retired manual process.

## Tests

`tests/test_job_alert_senders.py` (4), `tests/test_job_alert_scan.py`
(10, incl. dedup-across-runs, no-posting-url skip, thin-listing-still-saved,
extraction-failure-doesn't-mark-reviewed), `tests/test_inbox_accounts.py`
additions (9, incl. confirming the job-alert query/label never overlaps
with the CTA scan's own). Full suite: 394 passing.

## Not done / explicitly out of scope

- No live end-to-end verification against a real Gmail/Outlook/IMAP
  account or a real Anthropic API call from this sandbox - same honest
  limitation as every prior pass in this effort. The Settings UI itself
  couldn't be click-verified in the browser either: the license-gate
  email OTP requires a real code sent to Zahir's inbox, which this
  session correctly declined to try to work around (same call the
  native-packaging branch made when it hit the same gate) - verification
  here is careful manual code review plus the automated test suite.
- `docs/email-monitoring-task.md` is stale (describes the pre-
  `gmail_cta_scan.py` MCP design) - flagged for whoever owns doc cleanup,
  not touched here, per General's explicit agreement.
- Google Calendar-based "share real availability" for interview-scheduling
  replies is a separate, larger backlog item (already scoped and handed
  off earlier) - unrelated to this branch beyond both being inbox-adjacent
  work.
