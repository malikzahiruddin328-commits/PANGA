"""Covers the 2026-08-07 skip_reason fix: the inline "Pass" dialog's
"Something else" category now stores category+typed-detail "\n"-joined in
one skip_reason string (see ui/app.py's _pass_reason_dialog), instead of
discarding the category. write_dossier() renders skip_reason into
dossier.md as a human-readable summary - this covers that it splits the
two-line case into a nested bullet instead of letting markdown's
lazy-continuation fold it into one run-on line, while a plain single-string
skip_reason (every other category, or the older free-text-only
"Why not interested?" box) renders exactly as before."""

import pytest

from search.job_store import save_jobs
from tailoring.applications import upsert_application
from tailoring.dossier import write_dossier


@pytest.fixture
def job(isolated_data):
    j = {"source": "linkedin", "job_id": "1", "title": "Head of IT", "organization": "Acme Corp", "location": "Remote"}
    save_jobs([j])
    return j


def test_two_line_skip_reason_renders_as_a_nested_bullet(job):
    upsert_application(
        "linkedin", "1", status="not interested",
        skip_reason="Something else\nHiring manager gave off bad vibes on the phone screen.",
    )
    path = write_dossier("linkedin", "1")

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    assert "- **Skip reason:** Something else" in lines
    assert "  - Hiring manager gave off bad vibes on the phone screen." in lines


def test_plain_single_line_skip_reason_is_unaffected(job):
    upsert_application("linkedin", "1", status="not interested", skip_reason="Comp too low")
    path = write_dossier("linkedin", "1")

    content = path.read_text(encoding="utf-8")
    assert "- **Skip reason:** Comp too low" in content.splitlines()
