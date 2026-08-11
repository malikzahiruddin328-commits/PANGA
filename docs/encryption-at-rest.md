# Encryption at rest for data/

Implements `docs/frs.md` §7 / §15. Covers everything under `data/`: master profile
(`profile/structured/master_profile.json`), raw resume text
(`profile/raw/*.txt`, `profile/raw/manifest_result.json`), job history
(`jobs/jobs.json`), applications (`applications/applications.json`), and
CTA emails (`cta_emails/cta_emails.json`). `data/` stays local-only and
gitignored on top of this, unchanged from before.

## Why not a typed passphrase (`docs/frs.md` §7's original plan)

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
  Keychain if this ever runs on a Mac (see `docs/business-requirements-document.md` §5a), Secret Service on
  Linux. Same code path on every OS. No passphrase exists anywhere in this
  design.
- **Service/username used for the credential entry:** `Panga` /
  `data-encryption-key`.
- **Why keyring over calling DPAPI directly:** raw DPAPI (`CryptProtectData`)
  is Windows-only. `keyring` gets the same "auto-unlocks for this OS
  login, no passphrase" behavior while also covering the Mac port the
  BRD's §5a already plans for, at no extra cost.

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
    `InvalidTag` traceback, since `docs/frs.md` §6 rules out anything Zahir would
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

## Recovery (built 2026-07-30, closes the "Windows-account-loss recovery" backlog item)

Envelope encryption on top of the same key above - the AES-256-GCM key
protecting `data/` doesn't change, and no already-encrypted file needs
re-encrypting for this. Instead, a second, recoverable copy of that same
key is wrapped under a recovery code the user generates and saves
themselves.

- **Recovery code:** 20 random bytes (160 bits) from `secrets.token_bytes`,
  displayed as base32 in hyphenated groups of 4 (e.g.
  `URPH-3Y2W-V2VJ-J45C-HSZB-MI7Q-2QFO-E2I4`). High-entropy and random, not
  a memorized passphrase, so KDF slowness below is defense-in-depth rather
  than the primary defense against guessing.
- **Wrapping:** PBKDF2-HMAC-SHA256 (600,000 iterations, random 16-byte
  salt) derives a wrapping key from the recovery code's raw bytes: the
  data-encryption key is AES-256-GCM-encrypted under that wrapping key and
  saved to `data/security/recovery_envelope.json` (salt, iteration count,
  nonce, wrapped key - all safe to store as-is; useless without the code).
  Since `data/` is already gitignored/local-only, this file gets the same
  handling as everything else in it.
- **Generating a code:** Settings page, "Data Recovery" section -
  `generate_recovery_code()` in `crypto_store.py`. Shown exactly once, in
  the browser, with a "write this down now, save it somewhere other than
  this computer" warning; nothing about the plain code is ever written to
  disk. Generating again replaces the envelope and invalidates the
  previous code.
- **Recovering:** `scripts/recover_access.py` (double-click
  `recover_access.vbs` - no terminal, same pattern as the desktop
  shortcut's `run_app.vbs`/`run_app.bat`) - a small tkinter dialog that
  takes the code and calls `recover_key_with_code()`, which unwraps the
  key and reinstalls it into this account's credential store. Deliberately
  separate from the Streamlit app itself, since the app needs the key just
  to load its pages - it can't be the tool that recovers a missing one.
- **Safety fix bundled in:** before this, if the credential-store key ever
  went missing, `_get_or_create_key()` silently minted a brand-new random
  key and pressed on - meaning a lost/corrupted profile would produce
  confusing `InvalidTag` decrypt errors later instead of a clear signal
  up front. Now, a missing key is checked against whether a recovery
  envelope already exists: if one does, that's proof a real key was
  deliberately set up before, so this is unambiguously a recovery
  situation, not a fresh install - it raises a clear `RuntimeError`
  pointing at `recover_access.py` instead of fabricating a new key that
  could never have decrypted the existing files.
- **What this doesn't cover:** if the entire disk/machine is lost (not
  just the Windows account/profile), there's nothing to recover regardless
  of any of this - the code only helps when `data/` itself (including
  `recovery_envelope.json`) survives but the credential store doesn't
  (profile corruption/reset, or a deliberate move of `data/` to a new
  machine/account).
- **Verified 2026-07-30:** generated a code, deleted the real credential-store
  entry to simulate account loss, confirmed a normal read now fails loudly
  with the new RuntimeError (not a silent wrong key), confirmed a wrong
  recovery code is rejected without touching the credential store,
  confirmed the correct code restores the exact original key, and
  confirmed jobs/applications/cta_emails/profile all read back correctly
  afterward - tested both through `crypto_store.recover_key_with_code()`
  directly and through `recover_access.py`'s actual button-handler
  function (real `tkinter.Tk` instance, messagebox calls captured instead
  of shown).

## Known limits

- Single-machine only (see "Not a multi-machine sync solution" above) -
  the recovery code doesn't change this; it restores the same key on the
  same OS's credential store, not a way to read `data/` on a different OS
  without transferring the key some other way.
- The recovery code is only as safe as wherever the user stores it - if
  it's saved only on the same computer it's meant to recover, it doesn't
  survive whatever that computer loses.
