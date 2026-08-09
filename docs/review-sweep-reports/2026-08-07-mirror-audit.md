# Mirror audit — 2026-08-07

Automated `panga-mirror-audit` scheduled run. **Investigation only — no fixes
applied**, per Mirror's standing role as an independent reviewer.

> Delivery note: this run tried to report to the hub session ("General",
> `local_82edb105-4770-423e-9439-cb4260b13301`) via
> `mcp__ccd_session_mgmt__send_message`, which is **unavailable in unattended
> scheduled-task runs**. Landing the report here instead, following the
> existing `docs/review-sweep-reports/` convention. Hub: please pick this up
> and route the items at the bottom.

## What was done

- Read `CLAUDE.md` (incl. the "Be proactive, not reactive" section) and
  `docs/codemap.md` for orientation.
- Scoped to `git log --since="4 days ago" master` — ~60 commits, nearly all
  landed 2026-08-06. Read the diffs for the ~20 that changed real logic.
- Verified every checkable claim against the **real encrypted stores** via
  `venv\Scripts\python.exe` with `src` on `sys.path`, calling the project's own
  `load_jobs()` / `load_profile()` / `job_sources.load_job_sources()` /
  `boards._stable_job_id()` — not by reading code and trusting it.
- Ran the full suite: **363 passed, 81s, green.** Everything below is a
  coverage or correctness gap, not a failing test.

**Caveat on the numbers:** a live search run wrote to `jobs.json` mid-audit
(1225 → 1561 records between two reads). All figures below come from the later,
internally consistent read.

---

## F1 — CONFIRMED, HIGH
### The Dice JD-capture fix only exists on the path that isn't the daily one

**Claimed** — commit `c460f98` (08-06 10:23), *"Capture real JD text for Dice,
Workday, and SmartRecruiters jobs"*: "Dice: its own search response already
includes a real ~500-char JD excerpt in `summary` — just never captured.
Zero-risk fix: store it as `description` in `normalize_dice_job()`."

**Actually true** — one hour later, `1397cc9` (11:37) added `fetch_dice_jobs()`,
the direct scrape, and wired it in as `run_search.py` STEP 2c — the standalone
*unattended daily* path. That function never sets `description` at all
(`src/search/boards.py:211-228`). `docs/codemap.md:34` presents the direct
scrape as *the* Dice path.

**How verified** — production `jobs.json`. The two paths are distinguishable:
`normalize_dice_job()` emits a `salary_text` key, `fetch_dice_jobs()` doesn't.

| Dice path | records | with `description` |
|---|---|---|
| scrape (`fetch_dice_jobs`) | 119 | **0** (key absent entirely) |
| MCP (`normalize_dice_job`) | 210 | 93 |

Newest scrape batch written today, `2026-08-07T11:26`.

**Why it matters** — this is precisely the bug `c460f98` existed to close:
resumes drafted and ATS-scored with zero real JD content behind them. It is
reopened on the path that runs unattended.

**Test blind spot** — `tests/test_boards.py:173-219` tests description capture
for `normalize_dice_job` *and* explicitly documents ZipRecruiter/Indeed as
deliberately having none. Nothing covers `fetch_dice_jobs`' description either
way — the one path that actually runs is the one nobody pinned down.

---

## F2 — CONFIRMED, HIGH
### The cross-path dedup claim in `boards.py` is false in production

**Claimed** — `src/search/boards.py:213-219` (repeated at `:53-56`): "using the
same content-based id here too is what actually dedupes a posting found via both
paths, not just within this one."

**Actually true** — `_stable_job_id()` hashes `title|organization|location`, and
the two Dice paths format location differently:

| | scrape | MCP |
|---|---|---|
| | `Hybrid in Ann Arbor, Michigan` | `Ann Arbor, Michigan, USA` |
| | `No location provided` | `None` |
| | `Remote or Lacey, Washington` | `Lacey, Washington, USA` |

Different strings → different hash → dedup across paths is impossible.

**How verified** — recomputed against the real store: **187** `(title,
organization)` pairs exist on both paths. **0 share a `job_id`; all 187 differ.**
Example: "Chief Information Officer" @ University of Michigan — scrape
`fb137d77fb842c50`, MCP `61ffef5a1394913e`.

**Why it matters** — every Dice posting found by both paths is stored twice,
permanently, and will be on every future run. The docstring asserts the opposite
as settled fact.

---

## F3 — CONFIRMED, MEDIUM-HIGH
### The "54 duplicates cleaned up" state is already stale — 111 duplicate rows are back

**Claimed** — `aac13b7`, *"Fix unstable job_id … + clean up 54 accumulated
duplicates"*; `scripts/dedupe_boards_jobs.py` docstring: "Safe to re-run after
`--apply`: a second run finds no more duplicate groups once the first run's
already merged them."

**Actually true right now** — 234 records across Dice/Indeed/ZipRecruiter still
carry legacy pre-fix ids (raw UUIDs, `https://to.indeed.com/…` redirect URLs,
ZipRecruiter redirect URLs):

| source | records | legacy ids | **true unmerged duplicates** |
|---|---|---|---|
| Dice | 329 | 114 | **86** |
| Indeed | 138 | 82 | **10** |
| ZipRecruiter | 63 | 38 | **15** |
| **total** | **530** | **234** | **111** |

"True unmerged duplicate" = recomputing `_stable_job_id()` on the legacy record
lands on an id that already exists as a *separate* record. Net: **530 rows
representing 419 real postings — 111 redundant rows in the table Zahir looks
at.**

**Not purely historical residue** — every ZipRecruiter legacy record has
`date_added` `2026-08-06T15:10:42`, about 3 hours *after* `aac13b7` landed at
12:13. Dice and Indeed legacy records run to `15:10:42`/`15:10:43` too.

The script itself isn't broken — it groups by content, so a re-run would catch
these. The gap is that a one-off cleanup was treated as terminal while the
writer set is wider than `boards.py`, and F2 keeps manufacturing new ones. Same
shape as the original `job_id` bug that created this audit task.

---

## F4 — DESIGN CONCERN, MEDIUM
### Per-posting JD fetch is an unbounded serial N+1 on the daily run

Verified against real config, but this is a cost/architecture judgement, not a
data defect — flagging it as such rather than with the same confidence as F1–F3.

`search_workday_jobs()` / `search_smartrecruiters_jobs()` call
`_fetch_workday_job_description()` / `_fetch_smartrecruiters_job_description()`
once per posting, inside the result loop — each a serial
`requests.get(timeout=20)` (`src/search/company_sites.py`).

**Verified against real config:** `settings.yaml` has 5 `target_roles` (CIO,
SVP, VP, Head of IT, Director); `job_sources.yaml` limits sum to 45 → **225
serial detail fetches per daily run**, worst case ~75 minutes if endpoints hang.
Descriptions are fetched *before* `save_jobs()` dedupes, so already-stored
postings are re-fetched in full on every run.

The fetchers' docstrings address reachability ("reusing an endpoint already
proven reachable, not a new live-fetch risk") but never cost. `CLAUDE.md` is
explicit: "consider the cost of an operation before adding it to something that
runs 4x/day."

---

## F5 — CONFIRMED, LOW-MEDIUM
### The fail-soft JD fetch is completely silent, at any failure rate

Both fetchers are `except Exception: return None` with no logging
(`src/search/company_sites.py`).

**Verified in production:** AbbVie 10/244, IQVIA 3/48, Eisai 1/26 records have
no description — the AbbVie ones written `2026-08-06T15:14`, i.e. *after* the
fix. The fallback is firing at a low rate right now and nothing records it
anywhere.

If Workday changes its response shape, the failure mode is a 100% silent
regression to the original "drafting against no JD" bug, with no signal at all.
Exactly the masked-missing-capability pattern this audit exists to catch.

---

## F6 — CONFIRMED, LOW
### `aggregators.remaining_calls_today()` raises KeyError on a partially-written budget file

`_load_budget_state()` returns the raw YAML dict when `date == today`;
`remaining_calls_today()` then does `["calls"]` (`src/search/aggregators.py:125`).
A budget file with `date` but no `calls` → `KeyError`. `_save_budget_state()` is
a non-atomic `open(…, "w")` + dump, so an interrupted write produces exactly
that file.

**How verified** — constructed that file against the real module: raises
`KeyError: 'calls'`.

Dormant today — Adzuna is not configured (`is_configured()` False, settings
`aggregator_countries: None`), so `run_search` skips the whole step. Flagged
before it's switched on, not after.

---

## Also noted — not a finding

581 of 1561 job records have no `date_added` at all (USAJOBS 284, AbbVie 67,
Indeed 51, …). Looks like pre-feature historical residue rather than a live bug,
but it does mean any "how fresh is this job" logic can't rely on that field
being present.

---

## Suggested routing (hub's call, not Mirror's)

- **F1 / F2 / F3** are one cluster — they belong with whoever owns
  `search/boards.py` and the Dice work. F3 also needs a decision nobody has
  made: whether `dedupe_boards_jobs.py` should become a recurring step rather
  than a one-off script.
- **F4 / F5** belong with `company_sites.py`'s owner.
- **F6** is a one-liner for whoever next touches Adzuna.

Mirror has not fixed any of this and has not messaged the feature sessions
directly — routing through the hub, per the single-point-of-contact convention.
