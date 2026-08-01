# Tests

Regression suite (Zahir's request 2026-07-31: "create regression testing
[to] back test all that is developed and then keep on enhancing the test
pack after new features are added"). Run the whole suite:

```
venv\Scripts\python -m pytest
```

## Scope: pure logic only, no real API calls

Deliberate choice (confirmed with Zahir): this suite covers deterministic
business logic only - filtering rules, file naming, cost math, data-store
CRUD, status gating. It does **not** call the real Anthropic API (document
drafting, company/website lookups, Prospector Score computation) - those
cost real money per run and would make the suite slow and non-free to run
often. If API-call behavior needs verification, do it live (see the
project's own verification workflow) rather than adding it here.

## Isolation

Every store module (`job_store.py`, `applications.py`, `target_accounts.py`,
`dossier.py`, ...) hardcodes its own real data file path under `data/` at
import time. The `isolated_data` fixture in `conftest.py` monkeypatches all
of those path constants to a location under pytest's own `tmp_path` for the
duration of a test - **any test that reads or writes a Panga data store
must take this fixture**, so the suite can never touch real data even by
accident. Tests that only exercise pure functions (no store I/O) don't need
it - see `test_ranking_prioritize.py`, `test_company_filters.py`,
`test_clinical_trials.py`, `test_dossier_naming.py`, `test_api_cost.py` for
that pattern.

## Adding tests for new features

When a new feature lands:
1. If it's pure logic (a filter, a formatter, a schema helper, a pricing
   calculation) - write a plain test, no fixture needed.
2. If it reads/writes a data store - take the `isolated_data` fixture; if
   the store isn't covered by that fixture yet, add its `_PATH` constant
   to the fixture in `conftest.py` first.
3. If it makes a real Anthropic API call - do not add it here (see Scope
   above); verify it live instead.
4. Name the file `test_<module_name>.py` matching the source module it
   covers, so the mapping stays obvious as the suite grows.

## Support tab / Bhangi

The Support tab's issue store (`bhangi.issues`) isn't tested here - it lives
in the separate Bhangi project (`../Bhangi`); its own regression pack is
there.
