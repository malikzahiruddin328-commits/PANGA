# Manual "Send and receive" sync button — scope

Branch: `feature/manual-sync-button`. Started 2026-08-05, routed here by
the General hub session as Gmail-integration-adjacent work ("your domain
— you own `gmail_client.py`/`inbox_accounts.py`"), with a UI piece on top.

## Why this exists

`panga-cta-fulfillment` (the scheduled task that actually archives/
drafts/reconciles Call to Action + Prospector outreach items) was
throttled from every 10 minutes to 2x/day (8am/4pm) for cost reasons.
Zahir wanted a manual control so he's not stuck waiting for the clock -
same idea as Outlook's classic Send/Receive button. Design (a status card
- icon, "N updates to send", "last synced [time]", a "Send and receive"
button below) was approved directly by Zahir ("Option B").

## Key architecture decision

The button runs the fulfillment logic **synchronously, in-process, inside
Streamlit** - not by triggering the scheduled task or a live Claude Code
session. This is safe specifically because every provider client
(`gmail_client.py`/`microsoft_client.py`/`imap_client.py`) is already a
direct-API/OAuth/IMAP client with no MCP or live-session dependency
(confirmed zero `mailopoly`/MCP references anywhere in `src/` before
starting, same check done for the earlier multi-provider scan work).

**`src/fulfillment.py` is the shared implementation** of the archive/
draft/reconcile logic used by `scripts/cta_fulfillment.py` (a thin wrapper,
only actually invoked by the dormant Windows-Task-Scheduler path - see
below) and the dashboard button.

**Correction, 2026-08-11 (real doc-vs-code drift, found while investigating
a false "automation is broken" alarm):** the claim that used to be here -
"a manual sync and a scheduled run can never drift apart in behavior
because there's only one code path" - is **wrong** for the live 2x/day
schedule Zahir actually runs. That schedule is a Claude Code scheduled
task (`panga-cta-fulfillment`, `~/.claude/scheduled-tasks/`), which is a
**second, independent implementation** of this same archive/draft/
reconcile logic - it drives the Gmail MCP connector's tools directly, not
`src/fulfillment.py`. Three code paths exist in total for the same
underlying steps: this module (manual button + the dormant WTS script),
and the live scheduled task's own SKILL.md-driven reasoning. Concretely,
this drift caused a real bug: the dashboard's "last synced" status card
read only this module's `get_last_synced_at()`, which the live scheduled
task never updates - so the card sat stuck days-stale while the real
automation was running fine the whole time. Fixed 2026-08-11:
`get_last_synced_at()` now also reads the live task's own unconditional
hub-inbox report timestamps (see `fulfillment.py`'s `HUB_INBOX_DIR`) and
returns whichever source is more recent. The underlying three-
implementations-of-one-thing structure itself is unchanged - not
consolidated, just no longer silently misreported.

## What was already built vs. what this branch added

Contrary to the initial framing relayed from General ("real LLM reasoning
today, done live by the scheduled task's Claude session"), CTA reply
composition (`tailoring/cta_reasoning.py`'s `draft_cta_reply()`) was
**already** a direct Anthropic API call before this branch - built in an
earlier pass (native-packaging Phase 1 / the multi-provider-scan work).
That premise was stale, not something this branch needed to fix. Checked
the actual code before building rather than trusting the relayed
description, per the standing "verify before claiming a limitation" rule.

**What genuinely was still live-only and this branch actually converted**:
SKILL.md's STEP 2C, composing a cold Prospector outreach intro. New:
`src/prospector/outreach_reasoning.py`'s `draft_outreach_email()` - same
direct-API pattern, reuses `cta_reasoning.py`'s `_TARGETING_CONTEXT`
rather than duplicating Zahir's background paragraph a second time.

## What this branch built

- `src/fulfillment.py` - the shared archive/draft/reconcile logic:
  `fulfill_archive_requests`, `fulfill_cta_draft_requests`,
  `fulfill_outreach_draft_requests` (new), `reconcile_sent_drafts`,
  `reconcile_sent_outreach_drafts` (new), and the entry point
  `run_full_fulfillment()` used by both callers. Also owns
  `get_pending_count()` (archive + CTA draft + outreach draft pending,
  across every configured account) and `get_last_synced_at()`/
  `_record_sync_completed()` (new small store,
  `data/fulfillment/sync_status.json`, same encrypted + file-locked
  pattern as every other shared JSON store).
- `src/prospector/outreach_reasoning.py` - new, see above.
- `src/prospector/outreach.py` - gained `provider`/`account` fields (like
  `tailoring/cta_emails.py` got in the earlier multi-provider-scan pass)
  so fulfillment knows which configured account's client to route a given
  outreach record's draft-creation/reconciliation to; `gmail_draft_id`/
  `gmail_draft_link` renamed to `draft_id`/`draft_link` (read side in
  `src/ui/app.py` falls back to the old field names for existing stored
  records, same pattern used for `cta_emails.py`'s `web_link` rename).
- `src/ui/app.py`'s Call to Action tab: the status card + button
  (`_format_last_synced()` helper for the relative-time line), spinner
  while the synchronous call runs, toast summary + `st.rerun()`
  afterward so the count/last-synced line update immediately without a
  manual page refresh (CLAUDE.md's HCI checklist).
- `scripts/cta_fulfillment.py` rewritten to a thin wrapper calling
  `fulfillment.run_full_fulfillment()` - no logic duplicated between the
  scheduled path and the button.
- **Real bug found and fixed in passing, before it shipped behind this
  button**: `microsoft_client.py`'s `create_draft()` never extracted the
  bare address from a `"Name <email@domain.com>"`-formatted `to` field
  the way `gmail_client.py`/`imap_client.py` already do - `inbox_accounts.py`
  passes every provider a raw sender-header string uniformly, so an
  Outlook-routed CTA reply would have failed against Graph's API today.
  Fixed with the same `email.utils.parseaddr` extraction the other two
  clients use.
- 20 new tests (`test_fulfillment.py`, plus 2 new Microsoft-client tests
  for the address-extraction fix). One old test file
  (`tests/test_cta_fulfillment_routing.py`) removed - its coverage moved
  to `test_fulfillment.py`, testing the logic where it now actually
  lives. Full suite: 211 passing.

## Not done / explicitly out of scope

- No live end-to-end verification against a real Gmail/Outlook/IMAP
  account or a real Anthropic API call from this sandbox - same honest
  limitation as every prior pass in this effort.
- `docs/native-packaging-task-scheduler.md` and
  `scripts/install_scheduled_tasks.ps1`/`_packaged.ps1` (native-packaging
  branch territory, not touched here) still describe
  `Panga-CtaFulfillment` running every 10 minutes - that's the *future*
  Windows-Task-Scheduler-based packaged-app schedule, separate from the
  *live* Claude-scheduled-task system Zahir actually runs today (which
  General already retimed to 2x/day). Worth reconciling before
  native-packaging's installer ever actually gets activated for real, but
  that's that branch's call, not this one's.
- Outreach records that fail the "looks like a valid email" check (missing,
  malformed, or an obvious noreply address) stay pending forever, by
  design (SKILL.md: "skip, don't guess a fake address") - there's no
  in-app path to clear or fix that request today; a human has to correct
  the contact_email on the record. Worth a follow-up UI affordance if this
  turns out to be a real recurring papercut, not built here.
