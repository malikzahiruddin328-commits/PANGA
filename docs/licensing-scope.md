# Licensing / Subscription — scope

Branch: `feature/licensing`. Started 2026-07-31, split out of the "General"
brainstorming session into its own dedicated chat per Zahir's usual
one-session-per-purpose pattern. Full backlog context:
`docs/job-search-automation-prd.md` §13 — "Licensing / subscription +
per-user API key handling for a sold product" row.

## Goal

Panga is moving from a personal tool to something Zahir wants to sell.
This branch builds the subscription, activation, and licensing-enforcement
layer that makes that a real product rather than a copy-and-share `.exe`.

## Design (converged 2026-07-31, full detail)

### Business model
- 1-year subscription, billed via **Stripe** (Stripe Billing on top of card
  processing — no fixed monthly platform cost, only per-transaction fees:
  2.9% + $0.30 per charge, +0.5% of subscription volume on the Starter
  tier).
- **15-day free trial** before the first charge.
- Trial start is tracked **server-side**, tied to the customer's account/
  email — not a local file — so reinstalling the app doesn't reset the
  clock for free.

### Who pays for LLM usage
- Each customer connects their own Anthropic account (or creates one
  during setup) and Panga uses that account's API key directly for all
  reasoning calls (see native-packaging branch's "direct API" prerequisite
  — this depends on that work).
- Panga **never** meters or marks up API usage — the customer is billed by
  Anthropic directly. Matches the existing in-app principle (see
  `src/api_cost.py` and the Prospector website-lookup cost display) of
  never hiding or estimating real costs.
- API key stored locally via the existing `security.crypto_store`
  (AES-256-GCM, OS credential store) — same pattern as resume/profile
  data. Never sent to the license service.

### Activation and device binding
- **One device per license.**
- **Planned device move:** self-service, in-app. A "Deactivate this
  device" button (requires being online + authenticated) releases the
  binding immediately; the next device that logs in can then activate.
- **Unplanned move (device lost/stolen/dead, can't self-release):**
  routed through **manual support review** initially — customer emails
  support, Zahir verifies identity and releases the binding via the
  license service's admin view. Deliberately not building automated
  identity verification for this rare case at launch; automate later only
  if support volume justifies it.
- **Abuse guardrail:** rate-limit transfers — no more than one every 30
  days — so "transfer" can't be used as a rotating-device workaround for
  sharing one license.

### Offline / grace-period handling
- License check-in on app launch, plus a daily background check.
- **3-day offline grace period** before a hard lock, so a brief
  connectivity gap doesn't lock out a paying customer.
- **UI, three distinct states — do not conflate them, the right user
  action differs for each:**
  1. **Verified:** small persistent top-right indicator — "✓ License
     verified." Quiet, non-alarming (matches Panga's readability standard
     — `st.markdown`, not `st.caption`).
  2. **Can't verify / grace period (days 1-3 offline):** indicator changes
     to something like "⚠ License unverified — 2 day(s) left" with a
     **Refresh** button next to it that forces an immediate re-check-in.
  3. **Grace expired (no successful check-in for 3 days):** this can no
     longer be a small corner indicator — the app blocks use. Full-screen
     message explaining specifically that it's a *verification* failure
     ("We haven't been able to verify your license in 3 days — connect to
     the internet and reopen Panga"), not a billing problem, plus the same
     Refresh button.
  4. **Actually expired (trial ended / subscription lapsed, confirmed via
     a real successful check-in):** separate full-screen state, separate
     copy ("Your subscription ended on [date]"), with a renewal link
     instead of a Refresh button — a genuinely different problem needing a
     genuinely different action from state 3.

### Onboarding UX
- Single email-based screen — no separate login-vs-signup fork ("enter
  your email to continue" creates or logs in). Minimize fields, per the
  same instinct that killed the original 4-field LinkedIn paste form in
  favor of one PDF upload.
- Anthropic connect-or-create step in plain language, no unexplained
  jargon like "API key" dropped on a non-technical user cold. Draft
  wording (refine, don't treat as final):
  > "Panga uses Claude AI to do the actual reading and writing — you'll
  > need your own account with Anthropic (the company that makes it), so
  > you're only ever charged for what you personally use, never marked up
  > by us. [Connect an account I already have] [I don't have one — set one
  > up]"

### Backend
- A small **serverless license service** — a handful of HTTP endpoints
  (issue trial, check license status, release device binding, admin
  override) plus a managed database of license/device/trial-start
  records, driven by **Stripe webhooks** for renewal/cancellation/payment-
  failure events.
- Deliberately minimal infrastructure — not a service Zahir has to run and
  patch himself long-term. Same "least infrastructure that actually
  works" instinct behind choosing GitHub Releases over a custom update
  server for the update-mechanism branch.

## Explicit dependencies on the other two branches

- **`feature/native-packaging`**: this branch assumes that branch's
  "direct API" prerequisite (own Anthropic key, no live Claude Code
  session dependency) is done or far enough along — licensing a copy that
  still requires Zahir's own Claude Code session open doesn't make sense.
  Also needs to know the packaging branch's actual installed-app shape
  before finalizing where the license check hooks into app startup.
- **`feature/update-mechanism`**: a license check needs to survive an
  in-place update (don't force re-activation on every hotfix) — coordinate
  on how the local license/device state is stored so an update doesn't
  wipe or orphan it.

## Explicitly out of scope for this branch

- The direct-API rewrite itself (native-packaging branch's job).
- The update/hotfix delivery mechanism itself (update-mechanism branch's
  job).
- Marketing site, pricing page copy, or go-to-market — this is the
  technical enforcement layer only.

## A note on shared files

`src/ui/app.py` and similar shared files have a history of being touched
by multiple concurrent sessions at once (see the Panga project memory,
"Concurrent sessions" entry). Check `git diff` before committing here —
don't sweep up another branch's in-progress changes.
