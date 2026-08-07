"""Mirror's fine-needle audit findings (2026-08-06): (1) Settings claimed to
be "one shared place to manage everything Panga reads from," which was false
for the core drafting/scoring path - an uploaded resume only ever feeds
resume_text() (gap interview + target-role suggestions), never
master_profile.json (what generate_documents()/score_job() actually read).
(2) Two documents tagged "resume" get silently concatenated by resume_text()
with no indication to the user. These tests drive the real Settings tab (via
Streamlit's AppTest) to confirm the copy no longer makes the false claim and
that a duplicate-resume warning appears when it should.
"""

import pytest
from streamlit.testing.v1 import AppTest

from profile.ingest import ingest_uploaded_document

APP_PATH = "src/ui/app.py"


@pytest.fixture
def settings_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    return AppTest.from_file(APP_PATH)


def test_no_duplicate_resume_warning_with_a_single_resume(settings_app, monkeypatch):
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "extract_text", lambda file: "some resume text")
    ingest_uploaded_document(object(), "Resume.docx", "resume")

    at = settings_app
    at.session_state["active_tab"] = "settings"
    at.run(timeout=30)

    assert not at.exception
    assert not any("tagged \"resume\"" in w.value for w in at.warning)


def test_duplicate_resume_warning_shown_when_two_docs_tagged_resume(settings_app, monkeypatch):
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "extract_text", lambda file: "some resume text")
    ingest_uploaded_document(object(), "Old Resume.docx", "resume")
    ingest_uploaded_document(object(), "New Resume.docx", "resume")

    at = settings_app
    at.session_state["active_tab"] = "settings"
    at.run(timeout=30)

    assert not at.exception
    warnings = [w.value for w in at.warning if "tagged \"resume\"" in w.value]
    assert len(warnings) == 1
    assert "Old Resume.docx" in warnings[0]
    assert "New Resume.docx" in warnings[0]


def test_documents_section_no_longer_claims_to_be_the_shared_source_of_truth(settings_app):
    at = settings_app
    at.session_state["active_tab"] = "settings"
    at.run(timeout=30)

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "one shared place to manage everything Panga reads from" not in all_text.lower()
    assert "does **not** feed what gets drafted" in all_text or "master profile" in all_text
