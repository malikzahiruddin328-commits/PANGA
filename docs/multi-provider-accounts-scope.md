# Multi-provider email + calendar accounts — scope

Branch: `feature/multi-provider-accounts`. Started 2026-08-04, split into its
own dedicated worktree per Zahir's usual one-branch-per-purpose pattern -
requested directly in the native-packaging-phase2 session, which then set
this branch up rather than doing the work itself. Two related PRD backlog
items (see `docs/backlog-log.md` §13, being added by the
Backlog session as of this writing - not yet landed in this branch, which
started from master before that add).

## Why these two are one branch

Both are "add one more OAuth-backed account type to Panga's own direct-API
plumbing, no MCP connector, works from an unattended scheduled task or a
packaged install with no live Claude Code session" - the same constraint
`gmail_client.py` was originally built to satisfy for Gmail (see
`docs/native-packaging-scope.md`). Both also touch the same install-time
touchpoint (the account-setup wizard) and the same runtime shape (a
per-provider OAuth-or-IMAP client living in `src/`, token storage under
`data/`, callable from `scripts/*.py`'s unattended jobs). Splitting them
into two branches would mean duplicating that plumbing decision twice
instead of once.

## Part A — multi-provider email login

**Origin**: a "Mailopoly Inbox" MCP connector came up in a different
session as a possible multi-provider mail solution. Real problem with it:
paid third-party SaaS (mly.life, per-customer subscription on top of
Panga's own), and MCP-only - unreachable from `scripts/gmail_cta_scan.py`'s
unattended Task Scheduler run or a packaged install, the identical wall
`gmail_client.py` already exists to work around for Gmail. Presented to
Zahir directly (AskUserQuestion); he chose to build multi-provider support
directly into Panga instead of depending on Mailopoly.

**Providers**, per Zahir's explicit list (2026-08-04): Gmail (already
built), Outlook, Hotmail, Yahoo, "any other personal/ISP email" (his
examples: Verizon, BT Internet).

- **Outlook + Hotmail**: same provider under the hood (Microsoft folded
  Hotmail into Outlook.com years ago) - one Microsoft OAuth integration,
  but the account-setup wizard's picker must list both as separate,
  clearly-labeled options, since plenty of users still think of themselves
  as "Hotmail" users specifically and wouldn't think to pick "Outlook."
- **Yahoo**: IMAP + app-password flow (Yahoo doesn't offer a customer-
  facing OAuth app registration the way Google/Microsoft do for a small
  indie app) - same profile-icon > Manage account > Security >
  App passwords UX Mailopoly's own instructions use as a reference,
  without integrating Mailopoly itself.
- **"Other" (Verizon/BT Internet/any IMAP provider)**: generic IMAP +
  password/app-password. Design intent, confirmed with Zahir 2026-08-04:
  don't drop a bare "enter your IMAP server + port" box on a non-technical
  user. Auto-detect server settings from the email domain first (Mozilla's
  public ISPDB, the same lookup Thunderbird's autoconfig uses, plus an
  `imap.<domain>` guess fallback), and only fall back to asking for
  server/port by hand if auto-detect comes up empty.

**Not yet built as of this doc** - this section records the decision and
design intent this branch starts from, not completed work.

## Part B — calendar-aware availability in CTA interview-request replies

**Origin**: Zahir described a user story ("when an inbox hits with an
inquiry where they're asking for time to talk... look up the availability
and if the calendar is wide open we will show some busy slots... rather
than saying I am wide open do whatever you like") that he believed was
already built. Checked thoroughly (mega_1/mega_2 hub-session transcripts,
full-text search across every session) and found no trace of it as a
decision or as code - `tailoring/cta_reasoning.py`'s actual current
`draft_cta_reply()` behavior for an `interview_request` is just "confirm
enthusiasm and general availability, ask them to propose times." Zahir
installed a Google Calendar MCP connector as a result of not finding the
origin conversation, but that connector only works inside a live Claude
Code session - not from `scripts/cta_fulfillment.py`'s unattended run.

**Scope for v1, per the sizing given to Zahir 2026-08-04**:
- Google Calendar only - mirrors the "Gmail first" precedent from Part A;
  Outlook/Microsoft Calendar and Apple/iCloud Calendar are explicit
  follow-ons, not built together with this.
- OAuth + `freebusy.query` (busy/free windows only, no event titles/details
  - Panga never needs to know what's *on* the calendar, only when it's
  blocked; also the narrower, more privacy-respecting scope to request).
  Reuses `gmail_client.py`'s existing OAuth token-storage/refresh pattern;
  worth checking whether it can piggyback the *same* Google consent screen
  as Gmail login (one extra scope requested at once) rather than a fully
  separate wizard step.
- New logic: given real free windows over the next few business days,
  select a believable subset to offer (spread across different days, don't
  dump every open slot, don't cluster everything on the very next
  morning) - feeds into the *existing* `draft_cta_reply()` call as one
  more structured input, not a new call site. This is the one genuinely
  new design surface; expect to iterate on the heuristic with Zahir rather
  than get it right on the first pass.

**Not yet built as of this doc.**

## Status (2026-08-04, second pass)

Parts A and B above (the account-setup wizard's email/calendar login
flows) were built and merged into master on the original
`feature/multi-provider-accounts` branch. This second pass, on a fresh
`feature/multi-provider-scan` branch/worktree (the original branch's
worktree was cleaned up after merge), closes the gap flagged when that
work landed: connecting an Outlook/Yahoo/IMAP account through the wizard
didn't actually make the scheduled scan (`scripts/gmail_cta_scan.py`) or
fulfillment (`scripts/cta_fulfillment.py`) read that inbox - both were
still hardcoded to Gmail's client calls throughout, despite the wizard
letting you connect other providers.

**Zahir's explicit call on taking this on** (relayed via the General hub
session): yes, extend the scan now, not defer it - his framing was that
this *completes* the "no MCP anywhere in the app's own runtime" property
the whole multi-provider effort is built around, not a new scope
addition. Confirmed there are genuinely zero `mailopoly`/MCP references
anywhere in `src/` before starting.

**What this pass built**:
- `src/inbox_accounts.py` - the unified adapter `GmailAccount`/
  `MicrosoftAccount`/`IMAPAccount` classes present, plus
  `configured_accounts()` for discovery. "Reviewed"/"CTA" marking is
  Gmail labels / Outlook categories / a custom IMAP keyword flag
  (`PangaReviewed`/`PangaCTA`) - three different mechanisms behind one
  shared interface, chosen so a labeled message stays visible in INBOX
  for all three providers (not moved to a different folder, which would
  have been the simpler-but-wrong IMAP-only shortcut).
- `scripts/gmail_cta_scan.py` and `scripts/cta_fulfillment.py` rewritten
  to loop over `configured_accounts()` - sequential, not concurrent (see
  `inbox_accounts.py`'s own docstring for why), one account's failure
  logged and skipped rather than stopping the whole run. Filenames kept
  as-is despite no longer being Gmail-only, since Windows Task Scheduler
  references them by path.
- `tailoring/cta_emails.py` gained `provider`/`account` fields (both
  default to `"gmail"` for records/call sites predating this change) so
  fulfillment knows which account's client to route a given record back
  to, plus a `web_link` field (replacing the Gmail-only `gmail_link` name;
  read side falls back to the old key for pre-existing stored records).
  `mark_draft_created()` takes an explicit `draft_link` now instead of
  always synthesizing Gmail's URL format.
- `src/ui/app.py`'s Call to Action tab updated to match: provider-aware
  "Open in Gmail"/"Open in Outlook"/"Open inbox" button label, and a
  disabled-with-explanation fallback instead of a broken link when a
  provider (Outlook, IMAP) has no reliable webmail deep link.
- Real bug found and fixed *before* building further on it:
  `imap_client.py` was using IMAP sequence numbers (from plain
  `search()`/`fetch()`) as if they were persistent UIDs, passing them
  into a brand-new connection on every call - sequence numbers are only
  valid within the session that produced them. Switched every SEARCH/
  FETCH/COPY/STORE call to the `conn.uid(...)` variants; the test suite's
  FakeImap now hard-fails if the plain (non-UID) methods are ever called
  again, so this can't silently regress.
- 25 new tests across `test_inbox_accounts.py`, `test_cta_emails.py`, and
  `test_cta_fulfillment_routing.py` (the last one deliberately added
  outside the normal `src/`-only test scope, given how much new
  account-routing logic this pass introduced). Full suite: 180 passing.

**Not verified live** (same honest limitation as every OAuth/IMAP client
in this effort): no real Gmail/Outlook/IMAP account was scanned end to
end from this sandbox. Verification here was code-level - the adapter
tests confirm each provider's calls happen with the right arguments in
the right order, not that a real mailbox produces the expected result.

## Explicitly out of scope for this branch

- Outlook/Microsoft Calendar, Apple/iCloud Calendar - later follow-ons to
  Part B, not this pass.
- Calendar *write* access (creating/accepting events) - this branch is
  read-only availability lookup for drafting a reply; actually booking
  anything is a separate, bigger trust decision.
- The account-setup wizard's actual UI polish/HCI pass - build the
  provider logic first, wire into whatever wizard shape native-packaging's
  branch has by the time this needs to merge; coordinate before merge
  (see below).

## Coordination needed before merge

- **native-packaging**: the account-setup wizard this branch's providers
  plug into lives conceptually in that branch's Phase 2 scope
  (`docs/native-packaging-scope.md`). Check its current wizard shape
  before merging rather than assuming.
- **Backlog**: two `docs/backlog-log.md` §13 rows for this work were handed to the Backlog
  session 2026-08-04 (multi-provider email, calendar-aware CTA
  availability) - check they landed with the framing above before this
  branch reports "done."

## A note on shared files

`src/ui/app.py`, `docs/backlog-log.md`, and similar files
have a history of being touched by multiple concurrent sessions at once
(see the `project-panga-job-search-tool` memory, "Concurrent sessions"
entry, and native-packaging-scope.md's own version of this note). Check
`git diff` before committing here - don't sweep up another branch's
in-progress changes. This branch was deliberately started from master's
last *committed* state, not its working tree, specifically because master
had uncommitted PRD edits in flight when this worktree was created.
