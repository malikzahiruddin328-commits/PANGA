# Panga — Business Requirements Document (BRD)

Written 2026-08-01 at Zahir's explicit request. Consolidates business-level
decisions made across the "General" brainstorming session and the four
active feature branches (`feature/native-packaging`,
`feature/update-mechanism`, `feature/licensing`,
`feature/multi-vertical-generalization`) into one document. This is a
business/product document, not a technical spec — for implementation
detail, see `docs/frs.md` and each branch's own
`docs/*-scope.md`.

## 1. Executive Summary

Panga started as a personal job-search automation tool built for Zahir's
own executive (IT/CIO-track) job search. The decision as of 2026-07-31 is
to turn it into a sellable product: a paid, subscription-licensed,
natively-packaged Windows desktop application usable by job seekers across
any trade or industry, not just Zahir's own.

## 2. Business Objectives

- Convert a working personal tool into a real, sellable software product
  with minimal re-architecture — reuse everything already built (search,
  scoring, tailoring, Prospector, encryption, Learn Engine) rather than a
  rewrite.
- Reach a general job-seeker audience, not just IT/pharma executives —
  see §4 and `docs/multi-vertical-generalization-scope.md`.
- Keep the cost structure lean for a solo operator: usage-based billing
  (Stripe) with no fixed monthly platform costs, customers bring their own
  Anthropic account so LLM usage is never a cost or liability to Zahir.
- Distribute both directly (own installer) and via the Microsoft Store,
  once the product qualifies (see §7).

## 3. Business Model

- **Pricing**: $200/year, subscription, billed annually. **Explicitly a
  placeholder** — not validated against real costs. See the "Realistic
  cost evaluator" backlog item (`docs/backlog-log.md` §13) before treating this as final.
- **Trial**: 15 days free, no charge, before the first billing event.
- **Billing processor**: Stripe (Stripe Billing + card processing — 2.9%
  + $0.30 per charge, +0.5% of subscription volume on the Starter tier, no
  fixed monthly fee). Confirmed compatible with Microsoft Store policy
  10.8.6 for non-game subscription products (see §7) — no requirement to
  route billing through Microsoft's own commerce API.
- **LLM cost model**: each customer connects or creates their own
  Anthropic account during onboarding. Panga never meters, marks up, or
  becomes financially liable for a customer's LLM usage — Anthropic bills
  the customer directly. This is a deliberate cost-transparency principle
  already applied elsewhere in Panga (real per-call API cost displays).
- **Refund policy**: no refunds (US market). Confirmed by Zahir
  2026-07-31. Feeds into the Terms of Service/EULA (§8, not yet drafted).
- **Devices per license**: one device per license. Planned device moves
  are self-service (in-app deactivation); lost/stolen-device transfers go
  through manual support review, not full self-service automation, at
  launch scale.

## 4. Target Market

**Decision (2026-07-31): general job-search tool, not a pharma/life-sciences-
specific product.** Full design in
`docs/multi-vertical-generalization-scope.md`. Key implication: nothing in
the sold product can assume the buyer is on an IT/CIO career track or in
life sciences — target-role title ladders, industry-specific Prospector
signal sources, and personal disqualifiers must all become per-user
configuration generated from the customer's own resume and stated
industry/vertical, not Zahir's own hardcoded defaults. Prospector's
proactive signal-sourcing (openFDA, ClinicalTrials.gov) remains
life-sciences-specific at launch and expands to other verticals
incrementally, driven by real customer demand — not pre-built
speculatively for every trade.

**Concrete example used to stress-test this** (Zahir, 2026-07-31): a
plumber, a physician, a nurse practitioner, and a chemical engineer
targeting nuclear vs. plastics-forming plants all need the app to
recognize and work with *their* career, not default to executive IT
titles.

## 5. Product Scope (summary — see FRS for full detail)

Already built (personal-tool phase, see `docs/backlog-log.md`
for complete history): resume ingestion, gap-probing interview, multi-source
job search (USAJOBS, job boards, company ATS feeds, industry boards),
compatibility scoring, tailored resume/cover-letter drafting, encrypted
local data storage, Prospector (proactive target-account identification,
KPIs, outreach logging, Learn Engine), LinkedIn profile analysis, Gmail
call-to-action monitoring and fulfillment.

**In progress for the sold-product phase** (four active branches):
1. **Native packaging** (`feature/native-packaging`) — direct Anthropic
   API integration (replacing the live-Claude-Code-session dependency),
   Gmail's official API in place of the MCP connector, Windows Task
   Scheduler in place of Claude Code's scheduled tasks, PyInstaller +
   pywebview + Inno Setup packaging, uninstall/data-retention design.
2. **Update/hotfix distribution** (`feature/update-mechanism`) — GitHub
   Releases-based version check, auto-update on launch + manual
   check-now, rollback-safe apply step. **Reported complete** as of
   2026-08-01, pending native-packaging's final artifact shape.
3. **Licensing/subscription** (`feature/licensing`) — Stripe integration,
   device activation/transfer, offline grace handling, license-state UI.
4. **Multi-vertical generalization** (`feature/multi-vertical-generalization`)
   — per-user industry/vertical intake, resume-driven title-ladder
   reasoning, per-user target-role generation, user-editable
   disqualifiers.

## 5a. Platform Roadmap

Moved here from `docs/job-search-automation-prd.md` §5 on 2026-08-11
(Zahir's call, via Panga-Documentor: this is a business-level platform
commitment, not a functional spec item) — content unchanged.

- Prove out the workflow as a local tool/script first
- Package as a Windows desktop app once stabilized
- Port to Mac after that
- (Open question, deferred: whether native Windows app vs. cross-platform
  framework like Electron/Tauri gives better ROI — worth revisiting once
  the core logic is proven, since packaging choice shouldn't block MVP
  validation)

## 6. Non-Functional Requirements

- **Security**: existing AES-256-GCM encryption at rest (OS credential
  store key) carries forward unchanged. Race conditions, infinite loops,
  and locking are standing review criteria for any code change
  (`CLAUDE.md`).
- **Licensing enforcement**: one device per license, 3-day offline grace
  period before hard lock, distinct UI states for verified/grace/expired
  (see `docs/licensing-scope.md`).
- **Update safety**: checksum-verified downloads, backup-before-swap with
  rollback on failure (see `docs/update-mechanism-scope.md`).
- **Data portability/safety on uninstall**: user data and the encryption
  key are preserved by default; explicit opt-in required to delete, with
  a backup offered first (see `docs/native-packaging-scope.md`).

## 7. Distribution Strategy

**Two channels, not mutually exclusive:**
1. **Direct download** — Inno Setup installer from Zahir's own site/link.
2. **Microsoft Store** — broader discoverability, built-in trust signal
   for non-technical buyers. Requires meeting Store certification
   requirements (below) before submission.

### Microsoft Store qualifications (researched 2026-08-01, policy version
7.19, effective 2025-10-14 — verify against the live policy again before
actual submission, as Microsoft revises this periodically)

| Requirement | Detail | Panga impact |
|---|---|---|
| **Code signing** (10.2.9) | Installer binary and all PE files must be signed with a cert chaining to a Microsoft Trusted Root Program CA — self-signed certs rejected. | New cost item: purchase a code-signing certificate. Previously only noted as "deferred" in native-packaging's scope doc; now a hard Store-submission requirement, not optional polish. |
| **Third-party payment processor** (10.8.6) | Non-game PC products may use a secure third-party purchase API (e.g. Stripe) instead of Microsoft's in-product purchase API for subscriptions. | Confirms the existing Stripe-based licensing design needs no rework for Store distribution. |
| **Registration/payment UX** (10.8.2) | If registration or a payment transaction is required at install, it must happen in the app's own in-app experience; users may be directed to a browser only *after* installation completes. | The licensing branch's single-email onboarding screen must be the first thing shown in-app — cannot redirect straight to a browser-hosted signup at first launch. |
| **PCI DSS compliance** (10.8.2) | Required if the product or its processor collects credit card info. | Satisfied by using Stripe (handles PCI compliance) rather than collecting card data directly — already the plan. |
| **Company account likely required** (10.8.3) | Products requiring financial account info for primary functionality should be submitted from a Company account, not an Individual account. | Zahir likely needs a Microsoft Partner Center **Company** developer account, which requires business verification info — a real registration step, not yet done. |
| **Demo/test credentials** (10.3.1) | If login is required, a working demo account must be provided to Microsoft's reviewers via the certification notes. | Need a way to give reviewers a working license (e.g. a permanent test license bypassing the paywall) so they can actually exercise the app. |
| **Privacy policy** (10.5.1) | Mandatory for any product accessing Personal Information — explicitly named as applying to Win32/Desktop Bridge products. Must be linked in Partner Center, kept current as features change. | Feeds the not-yet-drafted Terms of Service/EULA/Privacy Policy backlog item (see §8) — this is now a hard submission blocker, not just good practice. |
| **Live Generative AI disclosure** (11.16) | Products with dynamic content from generative AI models responding to user input must: disclose AI use in Store metadata, note it in Partner Center at submission, ensure AI output complies with content policies, and provide a way for users to report bad AI-generated content — acted on by the developer. | New requirement, not previously scoped anywhere. Panga's existing point-and-talk feedback widget could double as the reporting mechanism, but this needs explicit confirmation and Store-listing copy calling out Claude AI usage. |
| **Clean uninstall** (10.2.7) | Product must clearly communicate and enable full uninstall. | Validates the uninstall design already written into `docs/native-packaging-scope.md`. |
| **Certification turnaround** | Up to 3 business days per submission for security/technical/content review. | Factor into release timelines — a rejected submission means another multi-day round-trip. |

## 8. Legal & Compliance Requirements (not yet drafted — real gaps)

- **Terms of Service / EULA / Privacy Policy** — required both for legal
  operation as a paid product handling resumes and Gmail data, and as a
  hard Microsoft Store submission requirement (§7). Not yet drafted.
- **Refund policy**: no refunds (US) — confirmed, needs to be reflected in
  the ToS once drafted.
- **Google API verification / CASA security assessment** — see the
  "Gmail OAuth scaling" backlog item below. Currently every customer must
  register their own Google Cloud OAuth client (Option 2, in progress);
  a single shared, Microsoft-verified OAuth app (Option 3) would remove
  this friction for non-technical customers but requires Google's app
  verification process, and — because Panga's Gmail scopes
  (`gmail.modify`, `gmail.compose`) are classified as **restricted
  scopes** — an annual third-party security assessment (CASA), which has
  real cost and lead time. Scoped as a backlog item, revisit once
  customer volume justifies the investment.

## 9. Risks & Dependencies

- **Cross-branch dependencies**: licensing depends on native-packaging's
  final artifact shape and direct-API prerequisite; update-mechanism
  depends on the same artifact shape; native-packaging's uninstaller
  depends on a device-release endpoint from licensing. See each branch's
  scope doc for the live coordination points.
- **Pricing is unvalidated** — $200/year has no cost-evaluator backing it
  yet (§3).
- **Gmail integration doesn't scale to non-technical buyers as currently
  designed** — Option 2 (guided per-customer OAuth wizard) reduces but
  does not eliminate this friction; a plumber or nurse practitioner may
  still struggle with Google Cloud Console's own UI even with in-app
  guidance. Option 3 is the real fix, gated on cost/time investment.
- **Prospector's value proposition is currently narrow** — its signal
  sourcing is life-sciences-specific; a general-audience customer outside
  that vertical gets a visibly thinner feature set at launch. This is a
  deliberate, disclosed tradeoff (§4), not an oversight.

## 10. Open Backlog Items Referenced by This Document

See `docs/backlog-log.md` §13 for the authoritative backlog.
Items this BRD depends on or surfaces:
- Marketing and sales strategy (not started)
- Realistic cost evaluator (not started) — needed before §3's pricing is
  final
- Terms of Service / EULA / Privacy Policy (not started) — now also a
  hard Microsoft Store submission blocker, not just a legal nicety
- Google OAuth scaling: shared verified app + CASA assessment (Option 3,
  not started, see §8)
- Microsoft Store submission prerequisites: code-signing certificate,
  Company developer account, demo/test license for reviewers, Live
  Generative AI disclosure + reporting mechanism (new, this document)

## 11. Real-Cost Table (researched 2026-08-01, verify before committing spend)

Every line item across this BRD and the backlog log with an actual dollar
cost, in one place. "$0" items are prioritized first per Zahir's explicit
instruction (2026-08-01) — nothing with a real cost gets scheduled
alongside the free work by default.

| Line item | Real cost | Frequency | Notes |
|---|---|---|---|
| Stripe card processing | 2.9% + $0.30/charge | Per transaction | Scales with revenue only, $0 upfront |
| Stripe Billing (subscriptions) | +0.5% of subscription volume | Per transaction | Same — no upfront cost |
| Supabase (licensing branch's backend) | $0 (free tier) → $25/mo (Pro) | Monthly, only once free-tier limits are exceeded | Free tier covers 500MB DB / 50,000 MAU / 5GB egress — almost certainly enough at launch scale |
| Code-signing certificate | ~$211-226/yr (Sectigo, cheapest legitimate option) up to $399-560/yr (DigiCert standard/EV) | **Annual, recurring** — a 2026 CA/Browser-Forum rule caps validity at ~1 year, no more multi-year one-time buys | Only needed for Store distribution or a Store-linked direct-download URL; avoided entirely by pure direct-download distribution |
| Microsoft Partner Center developer account | **$0** — corrected 2026-08-01; Microsoft removed the former $99 Company-account fee as of May 2026 | One-time | Both Individual and Company accounts are free to register now |
| Google CASA security assessment (Gmail OAuth scaling, Option 3 only) | $500-$4,500/yr depending on assessment tier | **Annual, recurring** | Google charges nothing directly — the fee goes to an independent third-party assessor. Deliberately deferred until real customer volume justifies it |

**Not yet priced** (out of scope until asked): a marketing/sales website's own domain and hosting costs, any paid marketing spend, legal review of the ToS/EULA if Zahir wants a lawyer's pass instead of a self-drafted version.
