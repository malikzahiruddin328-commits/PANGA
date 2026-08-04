# Panga Remediation Plan — 2026-08-01

Derived from [2026-08-01-review-sweep-report.md](2026-08-01-review-sweep-report.md). Covers `feature/multi-vertical-generalization` only — `feature/native-packaging` and `feature/update-mechanism` were not reviewed this round (both had large amounts of uncommitted, in-progress work; re-run the sweep once each is committed).

Ranked most severe first.

## 1. Settings tab silently overwrites the profile store on save

- **Branch / file:** `feature/multi-vertical-generalization` — `src/ui/app.py:394-476`
- **What's wrong:** The Settings tab now does `profile_for_settings = load_profile()` at render time, then `save_profile(profile_for_settings)` on click — a full read-modify-write of the entire `master_profile.json` just to persist the `seniority` field. If another session/tab calls `save_answer()` (Results tab "Save answers & regenerate resume", `src/profile/interview.py:46-62`) in the window between this tab's load and save, that update is silently dropped. This is a new writer added to a file the project's own coding standard (`CLAUDE.md`) explicitly calls out as shared state touched by multiple processes.
- **Suggested fix:** Don't round-trip the whole object. Either merge just the `seniority` key into a fresh `load_profile()` call taken immediately before `save_profile()`, or add a narrow `update_profile_field(key, value)` helper in `src/profile/storage.py` that does the merge atomically. Shrinks the lost-update window and makes the intent explicit in the code.
- **Source:** Code review, CONFIRMED, severity High.

## 2. `role_skills.json` writes are not atomic and will race once `detect_gaps()` gets a caller

- **Branch / file:** `feature/multi-vertical-generalization` — `src/skills/lookup.py:21-28` (`save_role_skills`)
- **What's wrong:** `save_role_skills` does a plain `Path.write_text()` with no temp-file+rename and no lock. The file was previously static/checked-in and read-only at runtime; this branch gives it its first runtime writer (`generate_target_roles()` in the Settings tab). Low risk today because `detect_gaps()`/`skills_for()` currently has no live caller (confirmed deliberate, per `docs/multi-vertical-generalization-scope.md:155-157`) — but the moment that feature is wired up, or if two Settings-tab sessions both trigger "Generate my target roles" at once, a concurrent reader could see a truncated/partial JSON write and throw `JSONDecodeError`.
- **Suggested fix:** Use the same atomic-write pattern as the project's other JSON stores — write to a temp file, then `os.replace()` over the target — before this branch sees any concurrent use, and definitely before `detect_gaps()` gets its planned caller.
- **Source:** Confirmed independently by both code review (CONFIRMED, Medium) and security review (PLAUSIBLE, Low–Medium) — same line, same root cause, one fix covers both.

## 3. Unguarded dict-key access in `generate_target_roles` can surface a raw traceback

- **Branch / file:** `feature/multi-vertical-generalization` — `src/tailoring/drafting.py:571-572`
- **What's wrong:** `data["ladder_industry"]`, `data["ladder_role"]`, and `data["title_ladder"]` are indexed directly with no `.get()` or validation. The JSON-schema `output_config` should guarantee these keys, but a schema-non-conforming API response would raise an uncaught `KeyError` that isn't caught by the `except (DraftingNotConfigured, DraftingFailed)` handler in `src/ui/app.py:797`, showing the user a raw Streamlit stack trace instead of a clean error.
- **Suggested fix:** Use `.get()` with a fallback, or wrap the call site with a broader exception handler that degrades to a user-facing error toast rather than a traceback.
- **Source:** Security review, PLAUSIBLE, Low.

## 4. LLM-generated industry/role labels aren't normalized before use as dict keys

- **Branch / file:** `feature/multi-vertical-generalization` — `src/tailoring/drafting.py:222-269` (`generate_target_roles`)
- **What's wrong:** `ladder_industry`/`ladder_role` strings proposed by the LLM are used directly as dict keys via `data.setdefault(industry, {})[role] = entries`. Minor spelling/casing drift between generations (e.g. "Financial Services / Banking" vs "Financial services/Banking") will silently create duplicate near-identical entries rather than updating the existing one, with no cleanup path.
- **Suggested fix:** Normalize (case-fold, collapse whitespace) before the `setdefault` lookup.
- **Source:** Code review, suggestion, category: maintainability/data quality.

## 5. No unit tests for the four new/changed functions

- **Branch / file:** `feature/multi-vertical-generalization` — `generate_target_roles`, `save_role_skills`, shared `resume_text()`, reshaped `save_answer`/`save_gap_answers` signatures
- **What's wrong:** None of these have unit test coverage, despite the project's "test before every commit" standard. The branch's own progress notes cite "70 existing tests pass" — that's regression coverage on old code, not new coverage on this diff.
- **Suggested fix:** At minimum, add a test for `save_role_skills`'s setdefault-merge behavior (confirm it doesn't clobber other industries' entries) and a test for `save_gap_answers`'s `is_disqualifier` routing.
- **Source:** Code review, suggestion, category: testing.

## 6. Two-store save with no partial-failure handling

- **Branch / file:** `feature/multi-vertical-generalization` — `src/ui/app.py:394-476`
- **What's wrong:** "Save settings" writes to `settings.yaml` (via `save_settings`) and `master_profile.json` (via `save_profile`) with no exception handling between the two calls. If the profile write throws (e.g. a keyring/DPAPI error), the user sees no error, and `settings.yaml` has already been silently updated while the profile write failed — a partial-failure state with no visible indication.
- **Suggested fix:** Wrap both writes in a single try/except and surface a clear error to the user if either fails, rather than leaving a silent partial state.
- **Source:** Code review, suggestion, category: correctness/error handling.

## 7. `load_profile()`'s new empty-dict default not exhaustively verified at every call site

- **Branch / file:** `feature/multi-vertical-generalization` — `src/profile/storage.py:11-12`
- **What's wrong:** The new `default={}` supports the fresh-install path (Settings tab now calls `load_profile()` unconditionally). Spot-checked `dossier.py:323`'s `.get("name")` and it's fine, but `tailor.py`, `interview_prep.py`, and `enhance.py` weren't exhaustively verified to tolerate an empty dict gracefully.
- **Suggested fix:** Quick pass over all `load_profile()` call sites to confirm none assume a populated dict.
- **Source:** Code review, suggestion, category: correctness (worth a quick check, not confirmed broken).

## 8. Resume-derived content now written in plaintext to a version-controlled file

- **Branch / file:** `feature/multi-vertical-generalization` — `src/skills/role_skills.json` (whole file)
- **What's wrong:** Generated ladder content (derived from the user's own resume) is written in plaintext into a file under `src/` rather than `data/`, where the project's `security.crypto_store` encryption-at-rest would apply. This matches the pre-existing pattern (original Lifesciences/Pharma entries were already plaintext here), so it's not a new regression — but it does mean resume-derived career details could now end up committed to git history whenever a new vertical is generated and the file is committed.
- **Suggested fix:** No action required to unblock this merge; worth a separate decision on whether `role_skills.json` should move under `data/` long-term now that it has a runtime writer for the first time.
- **Source:** Security review, CONFIRMED (pre-existing pattern, extended), severity Info.

---

**Not reviewed this round:** `feature/native-packaging` (26 files uncommitted, 1,068+/-527 lines) and `feature/update-mechanism` (1,603 lines of untracked new module/test code). Both showed signs of active mid-build work; re-run `/panga-review-sweep` once each is committed.

**Not run this round:** `/doctor` — unavailable in this non-interactive session.
