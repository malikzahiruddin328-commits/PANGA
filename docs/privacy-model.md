# Panga — Privacy & Trust Model

Status: **Draft for Zahir's review.** Written from the actual code and
design docs (`security/crypto_store.py`, `docs/licensing-scope.md`,
`docs/competitive-brief.md`), not aspirational marketing language. This is
meant to be the one place a skeptical friend-tester (or later, a buyer) can
read to find out exactly what Panga does with their data — everything here
should be checkable against the code, not just asserted.

Source backlog item: `docs/job-search-automation-prd.md` §13, "Privacy/trust
model — precise data-flow claim + published doc" row (added 2026-08-01).

---

## 1. The one-sentence version

**Panga runs entirely on your own computer. Your résumé, applications, and
job data are stored only on your machine, encrypted. The only things that
ever leave your computer are: (a) content you ask Panga to reason about,
sent to Anthropic using your own Anthropic account, and (b) — if you use
the voice feedback feature — a short audio clip sent to Google for
transcription.** Everything else below is the detail behind that sentence.

This is **not** "nothing leaves your machine" — it's "here is the complete,
short list of what does, and why." A vaguer claim would be easier to write
and harder to trust.

---

## 2. What Panga collects

Data Panga stores about you, gathered as you use it:

- **Résumé and profile** — the résumé/CV you upload, plus any context
  documents (reference letters, career notes) you add in Settings.
- **Job search data** — jobs found across every source (USAJOBS, Indeed,
  ZipRecruiter, Dice, industry-specific boards, direct company career
  sites), your fit scores, and which ones you've applied to.
- **Applications and generated documents** — tailored résumés, cover
  letters, exec bios, and their status (applied / interview / offer /
  rejected).
- **Gmail-derived data** — when the Gmail feature is connected, Panga reads
  application-related emails to detect calls-to-action (interview
  requests, assessment requests, rejections, recruiter questions) and can
  draft replies.
- **LinkedIn data you upload** — your exported LinkedIn profile PDF and, if
  provided, your connections CSV. Panga never logs into LinkedIn or scrapes
  it directly (LinkedIn's ToS blocks that) — everything here is a file you
  explicitly export and upload yourself.
- **Prospector data** — target companies, outreach logs, and the
  KPI/rejection-pattern data Prospector builds from your own application
  history.
- **Interview prep answers and feedback notes** — your own typed or
  voice-recorded notes, including the point-and-talk feedback widget.

## 3. Where it's stored

**All of the above lives only on your own machine**, in Panga's local
`data/` folder. Nothing in this list is uploaded to a Panga-run server —
there currently isn't one; the eventual license service (see §6) is a
separate, much smaller data flow.

- Encrypted at rest with **AES-256-GCM**. The encryption key is generated
  once and held in your operating system's own credential store (Windows
  Credential Manager / DPAPI on Windows; Keychain if this ever runs on a
  Mac) — not a password you type.
- **What this protects against:** someone copying your `data/` folder, or
  stealing/imaging your hard drive, cannot read it without your Windows
  login.
- **What this does *not* protect against** (disclosed plainly, not
  buried): anyone else signed into the **same Windows user account** as
  you can read the data, because the OS itself hands out the decryption
  key to anything running under that login. This is a limitation of the
  OS-credential-store approach, not a bug — see `crypto_store.py`'s own
  docstring. If you share a Windows login with someone else, Panga's
  encryption does not protect your data from them.
- **Recovery trade-off:** if your Windows account's credential store is
  ever lost (profile corruption, or you move `data/` to a new machine
  without it), the key is gone and `data/` becomes permanently unreadable
  — there is no other copy. A one-time recovery code (generated when you
  first set up Panga) is the only escape hatch; losing both the Windows
  login and the recovery code means permanent data loss. This is a real
  trade-off of "no cloud copy of your key," not an oversight.

## 4. What leaves your machine, and to whom

This is the complete list — if something isn't here, it isn't happening.

| What | Sent to | Why | Under whose account/billing |
|---|---|---|---|
| Résumé/job/application content, for AI reasoning, scoring, and drafting | **Anthropic** (Claude) | This is how Panga does the actual reading, writing, and scoring | **Your own** Anthropic account — Panga never sees, proxies, or marks up this usage; Anthropic bills you directly |
| Application-related emails (read) and draft replies (sent) | **Google (Gmail)** | Detecting calls-to-action and drafting replies | Your own Gmail account, via the `gmail.modify`/`gmail.compose` scopes |
| A short audio clip, if you use voice feedback | **Google's free Web Speech transcription endpoint** | Converting your spoken feedback note to text | No account/billing — free public endpoint, no API key involved |
| Job search queries (title, location, etc.) | **Job boards and career sites** (USAJOBS, Indeed, ZipRecruiter, Dice, industry boards, company Workday sites) | Searching for postings | Public search traffic — no personal profile/résumé data is sent, only search parameters |
| Signal-sourcing queries (company/drug/trial names) | **openFDA, ClinicalTrials.gov, PubMed** | Prospector's proactive company-targeting research | Public API queries — no personal data sent |
| (Once licensing ships) email, license key, device ID, Stripe billing events | **Panga's own license service + Stripe** | Subscription enforcement and payment processing | See §6 — explicitly **never** includes résumé/profile/application/job data |

**What is never sent anywhere:** your résumé/profile/application content
is never sent to Panga's own infrastructure (there isn't any today), never
sent to a third party other than the ones in the table above, and never
used to train any model — it goes directly from your machine to Anthropic
using your own account, governed by Anthropic's own data-retention and
no-training policy for API usage (see Anthropic's published API terms —
this is the one link in the chain outside Panga's control, and it's worth
reading directly rather than taking Panga's word for it).

## 5. Aggregate learning / telemetry — what Panga does NOT do (v1)

Decided 2026-08-01 (`docs/competitive-brief.md` §9), after an earlier,
looser framing ("everything learned gets shared to improve the product")
was deliberately walked back:

- **Panga ships with zero user-data-driven learning or telemetry in v1.**
  The trust claim, plainly: **nothing about your usage ever leaves this
  machine.** Not usage patterns, not outcomes, not anonymized counters —
  none of it leaves your device for product-improvement purposes.
  "Continuous improvement" for v1 means Zahir shipping better prompts and
  features from his own testing, delivered to you via ordinary app
  updates — not anything derived from your data. This is a scope
  decision, not a build item: no telemetry code exists in Panga today,
  and none should exist for v1.
- **If this ever changes**, it will be: opt-in, default-off, limited to
  structured counters/enums (never free text or document content), and
  reviewed against a written allowlist before any code ships — not added
  quietly. This is explicitly deferred until Panga has paying customers,
  and would be called out as a specific, separate consent step, not folded
  into a general terms update.
- **Permanently out of scope, not just deferred:** using your actual
  résumé/application text to fine-tune or train any model. That data is
  close to unique-per-person, which is exactly the condition under which
  model-memorization risks are real — not something Panga takes on at this
  stage regardless of product pressure to do so.

## 6. The eventual licensed product — what's different

Today, Panga runs as a personal tool inside your own Claude Code session.
If/when Zahir sells licensed copies, one new, narrow data flow is added —
kept deliberately separate from everything above:

- Panga's license service (Stripe-driven) receives **only**: your email,
  license key, device binding, and Stripe billing/subscription events.
- It **never** receives résumé, profile, application, or job data — those
  stay exactly as described in §2-§4, entirely local plus the Anthropic/
  Gmail flows you already control.
- Payment details themselves are handled by **Stripe**, not Panga — your
  card never touches Panga's own code or servers.

## 7. What Zahir is — and isn't — promising right now

Panga today is a **personal tool being dogfooded and shared for a
friend-test**, not a finished, audited commercial product. Being precise
about that distinction matters more than sounding reassuring.

**What Zahir is promising, and can back with the design above:**
- Your résumé/application/job data stays local, encrypted, under your
  control — this is architecturally true today, not a future intention.
- No hidden data flows: the table in §4 is the complete list, and stays
  current as the codebase changes (this doc is meant to be the thing that
  gets updated, not left stale).
- No AI usage markup or hidden LLM billing — you're billed by Anthropic
  directly, at Anthropic's own rates.
- No user-data-driven learning or telemetry in v1 (§5) — and if that ever
  changes, it'll be opt-in and disclosed specifically, not buried in a
  terms update.

**What Zahir is explicitly NOT promising:**
- A professional third-party security audit or penetration test — this
  has not happened.
- Protection against someone else signed into your same Windows login
  (§3) — that is a real, disclosed limitation, not a hypothetical edge
  case.
- Guaranteed data recovery — if you lose both your Windows credential
  store and your recovery code, your data is permanently unreadable; there
  is no support-ticket path to get it back.
- Uptime, support-response times, or availability guarantees of any kind
  during the friend-test period — this is pre-commercial software, used
  at the friend-tester's own risk, with bugs to be expected.
- That the encryption/architecture choices here have been reviewed by a
  lawyer or a professional security firm — they're sound engineering
  judgment (documented and checkable in the code), not a certified claim.

---

## 8. Feeds into

This document is the source of truth for precise data-flow language. It
should be read before drafting or updating:
- The Terms of Service / EULA / Privacy Policy (`docs/terms-and-privacy.md`)
  — legal language must match what's actually true here, not the reverse.
- Any marketing or positioning copy (`docs/competitive-brief.md`'s
  "local-first" claim) — that claim should link back to this doc's
  precise version, not stand alone.
- The eventual Microsoft Store privacy policy submission requirement
  (`docs/business-requirements-document.md` §7, policy 10.5.1).

**Open item:** this has not been reviewed by a lawyer. It's accurate to
the current code as of 2026-08-04 — if the code changes (a new data
source, a new third-party call), this doc needs a corresponding update
before the next release, not after.
