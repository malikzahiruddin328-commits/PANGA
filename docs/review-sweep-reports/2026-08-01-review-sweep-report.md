# Panga Review Sweep — 2026-08-01

## Environment check (`/doctor`)

Skipped. `/doctor` is a terminal-dialog command that opens an interactive panel and is not available in this non-interactive session. Run it manually from an interactive `claude` terminal if an environment check is needed.

## Worktrees discovered

| Branch | Worktree path | Commits since merge-base | Reviewed? |
|---|---|---|---|
| feature/multi-vertical-generalization | `.claude/worktrees/multi-vertical-generalization` | Yes | ✅ Reviewed |
| feature/native-packaging | `.claude/worktrees/native-packaging` | Yes | ❌ Skipped — active WIP |
| feature/update-mechanism | `.claude/worktrees/update-mechanism` | Yes | ❌ Skipped — active WIP |

### Skipped: feature/native-packaging

26 files with uncommitted changes: 1,068 insertions / 527 deletions across 15 tracked files, plus 4 new untracked files (`docs/native-packaging-task-scheduler.md`, `scripts/cta_fulfillment.py`, `scripts/gmail_cta_scan.py`, `scripts/install_scheduled_tasks.ps1`). This is a large, actively-changing diff — reviewing it now would review a moving target. Re-run this sweep once the owning session commits.

### Skipped: feature/update-mechanism

11 files touched: a small 84-line diff to tracked files (`docs/codemap.md`, `src/ui/app.py`), but a substantial **untracked** new module — `src/updater/` plus 6 new test files, 1,603 lines total, not yet committed. Same "mid-build" pattern as native-packaging. Re-run once committed.

---

## feature/multi-vertical-generalization

**Diff vs master:** 9 files changed, 624 insertions(+), 102 deletions(-)
(`docs/job-search-automation-prd.md`, `docs/multi-vertical-generalization-scope.md`, `src/profile/ingest.py`, `src/profile/interview.py`, `src/profile/storage.py`, `src/skills/lookup.py`, `src/skills/role_skills.json`, `src/tailoring/drafting.py`, `src/ui/app.py`)

**Summary:** Generalizes Panga's gap-detection/scoring/target-role logic away from being hardcoded to one user profile (Lifesciences/CIO). Adds a Settings-tab vertical/seniority intake, a `generate_target_roles()` LLM call that seeds the target-roles editor and writes a new `role_skills.json` entry, and a generic `is_disqualifier` flag replacing hardcoded CISO-rule text in the scoring prompt.

### Code Review — Verdict: **Request Changes**

#### Critical Issues

| File / Line | Issue | Severity | Status |
|---|---|---|---|
| `src/ui/app.py:394-476` | Settings tab now does a full `load_profile()` → `save_profile()` read-modify-write of the whole `master_profile.json` object just to persist the `seniority` field. If another session/tab calls `save_answer()` (Results tab, `src/profile/interview.py:46-62`) in between, that update is silently lost. New writer added to shared state the project's own CLAUDE.md flags as concurrently touched; Settings tab never wrote to the profile store before this branch. | High | CONFIRMED |
| `src/skills/lookup.py:21-28` (`save_role_skills`) | Read-modify-write on `role_skills.json` via plain `Path.write_text` — no temp-file+rename, no lock. File was previously static/read-only at runtime; this is its first runtime writer. Currently low real-world exposure (per `docs/multi-vertical-generalization-scope.md:155-157`, `detect_gaps()`/`skills_for()` has no live caller yet — dead-but-intentional), but becomes a real race the moment that future feature is wired up, or if two Settings-tab sessions hit "Generate my target roles" concurrently. | Medium (will escalate) | CONFIRMED |

#### Suggestions

| File / Line | Suggestion | Category |
|---|---|---|
| `src/ui/app.py:475-476` | Merge just the `seniority` key inside a fresh `load_profile()` taken immediately before `save_profile()` (or add a narrow `update_profile_field()` helper) instead of round-tripping the whole object. | Race condition |
| `src/skills/lookup.py:21-28` | Use the same atomic-write discipline as other stores (temp file + `os.replace`) rather than raw `write_text`. | Race condition / consistency |
| `src/tailoring/drafting.py:222-269` (`generate_target_roles`) | LLM-proposed `ladder_industry`/`ladder_role` strings are used as dict keys with no normalization — minor spelling/casing drift will create duplicate near-identical entries instead of updating the existing one. Normalize (case-fold, whitespace-collapse) before the `setdefault` lookup. | Maintainability / data quality |
| Multiple new functions | No unit tests added for `generate_target_roles`, `save_role_skills`, shared `resume_text()`, or reshaped `save_answer`/`save_gap_answers` signatures, despite the project's "test before every commit" standard. At minimum, test `save_role_skills`'s merge behavior and `save_gap_answers`'s `is_disqualifier` routing. | Testing |
| `src/ui/app.py:394-476` | "Save settings" writes to two stores (`settings.yaml`, `master_profile.json`) with no exception handling between them — a failure on the second write is invisible to the user, with `settings.yaml` already updated. | Correctness / error handling |
| `src/profile/storage.py:11-12` | `load_profile()`'s new `default={}` looks correct for the fresh-install path, but wasn't exhaustively verified against every call site (`tailor.py`, `dossier.py`, `interview_prep.py`, `enhance.py`) tolerating an empty dict. Spot-checked `dossier.py:323` only. | Correctness (worth a quick check) |

#### What Looks Good

- `resume_text()` dedup (`src/profile/ingest.py:75-83`) correctly consolidates two previously-duplicated implementations without changing the underlying path logic.
- Streamlit stale-widget pattern correctly avoided: `target_roles_gen_counter` folded into both session-state seed key and widget `key` (`app.py:429-459`) — the project's own documented fix for Streamlit ignoring new `value=` on an existing `key`.
- Disqualifier clarifying-question box uses an explicit caption + `placeholder=` rather than a hedged prefill (`app.py:1120-1129`) — correctly distinguishes "genuine judgment call" from "suggested guess" per the HCI standard.
- Industries/seniority/target-roles/job-series consolidated under one "Save settings" click rather than several.
- Toast messaging nudges the next step after a resume save or role-generation, keeping the result near the action.
- `is_disqualifier` generalization traced end-to-end (`drafting.py:716-731`, `interview.py:46-62`, `app.py:1093-1129`) — consistent, not half-wired.
- `detect_gaps()`/`role_skills.json`'s current orphan status is a documented, deliberate decision (`docs/multi-vertical-generalization-scope.md:144-157`), not a forgotten wiring gap.

### Security Review — Verdict: **Approve**

No injection, secrets-leak, SSRF, or unsafe-file-write vulnerabilities introduced. `src/ui/app.py` has zero `unsafe_allow_html`/`st.html` usage (confirmed via grep). No template engine touched. All new JSON handling uses standard `json.loads`/`json.dumps`. `resume_text()` reproduces the pre-existing file-read pattern, not a new path-traversal surface (`extracted_to` is `slugify()`-generated, not attacker-controlled).

| File / Line | Issue | Severity | Status |
|---|---|---|---|
| `src/skills/lookup.py:21-28` (`save_role_skills`) | Same non-atomic read-modify-write flagged in code review — a race could silently drop one write between concurrent `generate_target_roles()` calls. | Low–Medium | PLAUSIBLE |
| `src/tailoring/drafting.py:571-572` (`generate_target_roles`) | `data["ladder_industry"]`, `data["ladder_role"]`, `data["title_ladder"]` indexed directly with no `.get()`/validation. A schema-non-conforming API response raises an uncaught `KeyError` not covered by the `except (DraftingNotConfigured, DraftingFailed)` handler in `app.py:797`, surfacing a raw traceback. | Low | PLAUSIBLE |
| `src/skills/role_skills.json` (whole file) | Generated ladder content derived from resume text is written in plaintext under `src/` (version-controlled), not under `data/` where encryption-at-rest applies. Matches the pre-existing pattern (original entries were already plaintext here) — not a regression, but resume-derived career details could now end up in git history when a vertical is generated and committed. | Info | CONFIRMED (pre-existing pattern, extended) |

No findings for: hardcoded/logged credentials, SSRF, unsafe deserialization, command injection, or path traversal via user-controlled filenames.

---

## Overall flags for Zahir

- 🔴 **CONFIRMED, High**: Settings-tab full-profile overwrite race (`app.py:394-476`) — genuinely new regression, easy fix, should land before merge.
- 🟠 **CONFIRMED, Medium**: `save_role_skills` non-atomic write (`lookup.py:21-28`) — low risk today, will become live once `detect_gaps()` gets a caller. Same fix covers both the code-review and security-review findings on this line.
- Everything else is a suggestion/hygiene item, not a blocker.
