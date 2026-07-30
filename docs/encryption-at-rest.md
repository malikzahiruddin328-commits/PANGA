# Encryption at rest for data/

Implements PRD §7 / §15. Covers everything under `data/`: master profile
(`profile/structured/master_profile.json`), raw resume text
(`profile/raw/*.txt`, `profile/raw/manifest_result.json`), job history
(`jobs/jobs.json`), applications (`applications/applications.json`), and
CTA emails (`cta_emails/cta_emails.json`). `data/` stays local-only and
gitignored on top of this, unchanged from before.

## Why not a typed passphrase (PRD §7's original plan)

§7 originally called for the user to set a passphrase at first setup as
"the only key." That was written before the scheduled tasks in §13/§14
existed. Those tasks (`panga-daily-job-search`, `panga-gmail-cta-scan`,
`panga-cta-fulfillment`) run unattended, one to several times a day, with
nobody present to type a passphrase - a pure passphrase model would break
every one of those runs. Caching a typed passphrase to disk so scheduled
tasks could read it unattended would have weakened the model to roughly
the same level as what's built below anyway, with more moving parts.

## Key model

- **Algorithm:** AES-256-GCM (authenticated encryption - a corrupted or
  tampered file fails to decrypt loudly rather than silently returning
  garbage).
- **Key:** a random 256-bit value, generated once on first use.
- **Key storage:** the OS credential store, via the `keyring` Python
  library - Windows Credential Manager (DPAPI-backed) on this machine,
  Keychain if this ever runs on a Mac (see PRD §5), Secret Service on
  Linux. Same code path on every OS. No passphrase exists anywhere in this
  design.
- **Service/username used for the credential entry:** `Panga` /
  `data-encryption-key`.
- **Why keyring over calling DPAPI directly:** raw DPAPI (`CryptProtectData`)
  is Windows-only. `keyring` gets the same "auto-unlocks for this OS
  login, no passphrase" behavior while also covering the Mac port PRD §5
  already plans for, at no extra cost.

**What this protects against:** the files being copied off this machine,
or the disk being stolen/imaged - useless ciphertext without this specific
Windows account's credential store.

**What this does *not* protect against:** someone else using this same
Windows login. That's the accepted tradeoff for keeping unattended
scheduled tasks working (see above) - narrower than a passphrase would be.

**Not a multi-machine sync solution:** each OS's credential store holds
its own independent copy of the key. Encrypting on this Windows machine
and reading the same `data/` files as-is from a future Mac install would
not work without separately transferring the key. Out of scope while §12
keeps this single-machine/single-user - revisit if that changes.

## Implementation

- `src/security/crypto_store.py` - the only place that touches the crypto
  primitives. Exposes `read_json`/`write_json`/`read_text`/`write_text`
  (plus lower-level `read_bytes`/`write_bytes`/`encrypt_bytes`/
  `decrypt_bytes`), each a drop-in replacement for the plain
  `Path.read_text`/`write_text`/`json.loads`/`json.dumps` calls the store
  modules used before.
  - Ciphertext format on disk: a 9-byte magic header (`PANGAENC1`) + a
    12-byte random nonce + the AES-GCM ciphertext (tag included). The
    magic header lets `is_encrypted()` tell ciphertext apart from
    not-yet-migrated plaintext by structure, not by guessing from
    content - used by the migration script below so it's safe to re-run.
  - A failed decrypt (wrong key / corrupted file) raises a `ValueError`
    with a plain-language message rather than surfacing a raw
    `InvalidTag` traceback, since PRD §6 rules out anything Zahir would
    need to debug himself.
- **Store modules updated to call it** (interface unchanged - only the
  read/write internals moved): `profile/storage.py`, `search/job_store.py`,
  `tailoring/applications.py`, `tailoring/cta_emails.py`,
  `profile/ingest.py` (writes raw text + manifest), `profile/interview.py`
  (reads raw text + manifest back for gap detection).
- **Migration:** `scripts/encrypt_existing_data.py` - one-time script that
  encrypts the plaintext files that existed before this was built. For
  each file: encrypts to a sibling temp file, decrypts that temp file back
  and byte-compares it against the original before replacing anything, so
  a failure partway through never leaves a half-migrated file. Skips files
  already in the encrypted format (via the magic header), so it's safe to
  run again. Already run once (2026-07-30) - migrated all 12 files
  present at the time, verified against a pre-migration backup (jobs:
  368, applications: 2, cta_emails: 2, gap_interview_answers: 7 - all
  matched).

## Scheduled tasks

No changes needed on the scheduled-task side - they run as the same
Windows user as the interactive Streamlit session, so the credential-store
key unlocks for them the same way. Confirmed via the Streamlit app
(equivalent unattended-process case) after the migration: Results,
Call to Action, and Settings pages all load correctly against the
now-encrypted files, no errors in console or server logs.

## Known limits

- Losing this Windows user account/profile (corruption, reinstall) loses
  the credential-store key and therefore the data, with no recovery path
  today - re-scoped backlog item, PRD §13 ("Windows-account-loss
  recovery").
- Single-machine only (see "Not a multi-machine sync solution" above).
