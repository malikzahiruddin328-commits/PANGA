# Panga — Terms of Service, EULA & Privacy Policy (Draft)

**Status: Draft for Zahir's review — not legally reviewed.** Written via
reasoning against Panga's actual design docs (`docs/licensing-scope.md`,
`docs/privacy-model.md`, `docs/business-requirements-document.md` §7), at
$0 cost per Zahir's 2026-08-01 prioritization instruction (draft first via
reasoning + his own review; a lawyer pass is a later, funded step, not a
blocker to having something real to show a friend-tester or the Store).

Every factual claim in the Privacy section is required to match
`docs/privacy-model.md` exactly — that document is the source of truth for
what Panga actually does with data; this document turns it into the legal
language a user agrees to. If the two ever disagree, `privacy-model.md` is
right and this doc needs to be corrected, not the other way around.

**Placeholders Zahir needs to fill in before this is usable** (marked
`[FILL IN]` throughout): legal entity name/structure, business address,
support contact, governing-law jurisdiction, and the final subscription
price once the "Realistic cost evaluator" backlog item lands. Everything
else is ready for his edits.

---

## Part 1 — Terms of Service

**Last updated:** [FILL IN — date of first publication]

By installing or using Panga ("the App," "the Software"), provided by
`[FILL IN — Zahir's legal name or business entity]` ("we," "us," "Panga"),
you agree to these Terms of Service, the End User License Agreement
(Part 2), and the Privacy Policy (Part 3) below.

### 1.1 What Panga is

Panga is a personal job-search automation tool: résumé/profile
management, job discovery and fit-scoring across multiple job boards,
application tracking, AI-assisted document drafting, and a proactive
targeting layer ("Prospector"). It runs on your own computer.

### 1.2 Accounts, subscription, and billing

- Panga is offered as a **1-year subscription**, currently priced at
  **$[FILL IN — placeholder was $200/year, pending final cost analysis]
  per year**, billed through **Stripe**.
- New subscriptions include a **15-day free trial**. Your trial start
  date is tracked by our license service and tied to your account/email
  — reinstalling the app does not reset it.
- **Billing and payment processing are handled entirely by Stripe.** We
  never see or store your full card details.
- **All sales are final — no refunds**, except where required by
  applicable law. If you're unsure whether the trial period is enough to
  evaluate Panga, use it before your card is charged.
- You may cancel auto-renewal at any time before your next billing date
  to avoid the next year's charge; cancellation does not retroactively
  refund the current subscription period.

### 1.3 Your Anthropic account (AI usage)

Panga uses Anthropic's Claude to do its actual reasoning, writing, and
scoring. **You connect your own Anthropic account** (or create one during
setup), and Anthropic bills you directly for that usage, at Anthropic's
own published rates. Panga does not meter, mark up, or add any charge on
top of your Anthropic usage — see the Privacy Policy (Part 3) for exactly
what content is sent to Anthropic and why.

**AI-generated content disclosure:** résumés, cover letters, interview
prep material, and other documents Panga produces are generated with the
assistance of Claude, an AI system. Review AI-generated content before
relying on it or submitting it as your own work — Panga does not
guarantee the accuracy, tone, or appropriateness of AI-generated text for
any specific employer or role.

**Reporting bad AI output:** if Panga's AI-assisted output is inaccurate,
inappropriate, or otherwise concerning, use the in-app feedback widget
(the "Leave feedback on this screen" recorder present on every tab) to
report it. We review feedback submitted this way and use it to improve
Panga's prompts and behavior.

### 1.4 Device activation

- Your license activates on **one device at a time**.
- To move to a new device, deactivate your current device in-app first
  (self-service, requires being online).
- If your device is lost, stolen, or broken before you could deactivate
  it, contact `[FILL IN — support email]` for manual review — we verify
  your identity and release the binding on our end.
- Device transfers are limited to **one every 30 days** to prevent license
  sharing.

### 1.5 Acceptable use

You agree not to:
- Share your license, account, or Anthropic credentials with others.
- Attempt to circumvent license activation/device-binding checks.
- Use Panga to violate the terms of service of any job board, career
  site, or platform it connects to (e.g., LinkedIn — Panga deliberately
  does not scrape or auto-login to LinkedIn for this reason; you upload
  your own exported LinkedIn PDF/CSV instead).
- Use Panga for any unlawful purpose.

### 1.6 Friend-test / pre-release status

**If you are using Panga as part of an early friend-test rather than a
paid subscription:** Panga is pre-commercial software. It may contain
bugs, may change significantly, and is provided with no uptime, support
response time, or availability guarantee. See Part 2 §2.4 ("As-Is,"
warranty disclaimer) — the same disclaimer applies, without the paid
subscription terms in §1.2-1.4.

### 1.7 Termination

We may suspend or terminate your license if you violate these Terms
(§1.5) or engage in license-sharing/abuse (§1.4). You may stop using
Panga and cancel your subscription at any time (§1.2 governs refund
treatment).

### 1.8 Changes to these Terms

We may update these Terms as Panga's features change. Material changes
(e.g., a new data flow, a change to the refund policy) will be flagged
in-app, not just silently updated in this document.

### 1.9 Governing law

These Terms are governed by the laws of `[FILL IN — Zahir's jurisdiction,
e.g. state/country]`, without regard to conflict-of-law principles.

---

## Part 2 — End User License Agreement (EULA)

### 2.1 License grant

Subject to these Terms and payment of applicable subscription fees, we
grant you a limited, non-exclusive, non-transferable, revocable license
to install and use Panga on one device at a time, for your own personal
job search use.

### 2.2 Restrictions

You may not: copy, modify, or create derivative works of Panga; reverse
engineer, decompile, or disassemble Panga except where applicable law
expressly permits it; redistribute, resell, sublicense, or share your
copy or license with others; remove or alter any proprietary notices.

### 2.3 Ownership

Panga and all associated intellectual property remain the property of
`[FILL IN — legal entity]`. This license gives you the right to use the
software, not ownership of it. Documents Panga generates for you (your
résumé, cover letters, etc.) are yours.

### 2.4 Warranty disclaimer ("As-Is")

Panga is provided **"as is" and "as available," without warranty of any
kind**, express or implied, including but not limited to warranties of
merchantability, fitness for a particular purpose, and non-infringement.
We do not warrant that Panga will be error-free, uninterrupted, or that
any job application outcome (interviews, offers) will result from using
it.

### 2.5 Limitation of liability

To the maximum extent permitted by law, `[FILL IN — legal entity]` is not
liable for any indirect, incidental, special, or consequential damages
arising from your use of Panga, including lost job opportunities, lost
income, or data loss (see the Privacy Policy §3.3 for the specific,
disclosed data-recovery limitation). Our total liability for any claim
is limited to the amount you paid for your current subscription term.

### 2.6 Data loss disclosure

Panga stores your data locally, encrypted with a key held in your
operating system's credential store. **If that credential store is lost
and you don't have your recovery code, your data becomes permanently
unreadable — we cannot recover it for you.** See Privacy Policy §3.3 for
the full explanation. By using Panga, you acknowledge this risk.

---

## Part 3 — Privacy Policy

**This section must stay word-for-word consistent with
[`docs/privacy-model.md`](privacy-model.md) — that document is the
technical source of truth; treat any drift between the two as a bug in
this document, not a rewording choice.**

### 3.1 What we collect

Panga stores your résumé/profile, job search results and fit scores,
application records and generated documents, Gmail-derived
call-to-action data (if you connect Gmail), LinkedIn data you upload
yourself, Prospector targeting/outreach data, and interview prep/feedback
notes you enter. Full detail: `privacy-model.md` §2.

### 3.2 Where it's stored

**Locally, on your own device, only.** Encrypted at rest with
AES-256-GCM; the key lives in your OS credential store (Windows
Credential Manager/DPAPI), not typed by you and not stored by us. We do
not operate a server that stores your résumé, application, or job data.
Full detail: `privacy-model.md` §3.

### 3.3 Limitations, disclosed plainly

- Encryption protects your data if your device is stolen or your files
  are copied elsewhere. **It does not protect your data from another
  person signed into the same Windows account as you.**
- If your OS credential store is lost and you don't have your one-time
  recovery code, your data is **permanently unreadable** — there is no
  other copy, and we cannot recover it for you.

### 3.4 What leaves your device, and to whom

The complete list — nothing beyond this table:

| Data | Recipient | Purpose |
|---|---|---|
| Résumé/job/application content | Anthropic (your own account) | AI reasoning, scoring, drafting |
| Application emails (read) / drafted replies (sent) | Google (your own Gmail account) | Detecting and responding to application-related emails |
| Voice feedback audio, if used | Google's free Web Speech transcription endpoint | Converting your spoken note to text |
| Job search terms (title, location) | Job boards/career sites you search | Finding job postings |
| Company/drug/trial names | openFDA, ClinicalTrials.gov, PubMed | Prospector's public-data company research |
| Email, license key, device ID, billing events | Our license service + Stripe | Subscription enforcement and payment |

Full detail and explanation: `privacy-model.md` §4.

### 3.5 What we do with your data

We do not sell your data. We do not use your résumé, application, or job
content to train any AI model. **We do not collect usage telemetry or
analytics from your use of Panga in the current version** — see
`privacy-model.md` §5 for the full explanation and what would have to be
true before that ever changes (opt-in, disclosed separately, never
without a written review).

### 3.6 Your rights

Since your data lives on your own machine under your control, you can
view, export, or delete it directly at any time — there is no separate
data-request process needed for data that never left your device. For
data that *is* held by us (§3.4's license/billing row), contact
`[FILL IN — support email]` to request access or deletion.

### 3.7 Children's privacy

Panga is not directed to, and should not be used by, anyone under 18.

### 3.8 Changes to this policy

If a new data flow is added (a new integration, a new third-party call),
this policy and `privacy-model.md` will both be updated before that
feature ships, and the change will be flagged in-app rather than silently
published.

### 3.9 Contact

Questions about this policy: `[FILL IN — support email]`.

---

## Notes for Zahir (remove before publishing)

- This satisfies the Microsoft Store's mandatory privacy-policy
  requirement (policy 10.5.1) once the `[FILL IN]` fields are filled and
  it's hosted at a stable, linkable URL — Partner Center requires a live
  link, not a bundled file.
- Policy 11.16 (Live Generative AI content) is addressed in §1.3's
  disclosure + reporting-mechanism language, reusing the existing
  point-and-talk feedback widget as the reporting channel — flagged in
  the BRD as needing explicit confirmation this counts, not an assumption
  to publish on.
- Recommend an actual lawyer pass before this is used for real paid
  transactions, even though the $0-cost draft-via-reasoning path was the
  right first step per your 2026-08-01 prioritization call. This is
  drafted to be accurate and internally consistent, not to be a
  substitute for legal review at commercial scale.
