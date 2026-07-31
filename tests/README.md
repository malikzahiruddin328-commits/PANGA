# Tests

Basic tests get added per module as it's built, starting with resume ingestion.

Run with `pytest` from the repo root (needs the `pytest` package from
`requirements.txt`). `conftest.py` adds `src/` to the import path the same
way `src/ui/app.py` does at runtime.

The Support tab's issue store (`bhangi.issues`) isn't tested here - it lives
in the separate Bhangi project (`../Bhangi`); its own tests are there.
