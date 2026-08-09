"""Real gap General/Zahir caught before this feature shipped (2026-08-09):
blocking only the "applied" status transition and the Apply Assist render
still left the actual, real-filename .docx sitting on disk with an
unresolved "?" in it - fully double-clickable/attachable from File
Explorer regardless of either in-app gate. These tests exercise the real
guarantee: sync_workspace_documents() itself never writes the real
expected filename from text that still has a "?" in it."""

import pytest

from search.job_store import save_jobs
from tailoring.applications import upsert_application
from tailoring.dossier import _workspace_filename, dossier_dir, sync_workspace_documents


@pytest.fixture
def job(isolated_data):
    import profile.storage as storage
    storage.save_profile({"name": "Jane Doe"})

    j = {"source": "linkedin", "job_id": "1", "title": "Head of IT", "organization": "Acme Corp", "location": "Remote"}
    save_jobs([j])
    return j


def test_unconfirmed_resume_text_is_not_written_to_the_real_filename(job):
    body = "PROFESSIONAL EXPERIENCE\nLed a team of 8-10 engineers?\n"
    upsert_application("linkedin", "1", status="under review", resume_text=body)
    sync_workspace_documents("linkedin", "1", ["resume"], {"resume": body}, {"name": "Jane Doe"}, job)

    folder = dossier_dir("linkedin", "1", "Acme Corp", "Head of IT")
    real_path = folder / _workspace_filename("resume_text", "Jane Doe", job)
    assert not real_path.exists()


def test_unconfirmed_resume_text_is_written_to_a_draft_unconfirmed_filename(job):
    body = "PROFESSIONAL EXPERIENCE\nLed a team of 8-10 engineers?\n"
    upsert_application("linkedin", "1", status="under review", resume_text=body)
    sync_workspace_documents("linkedin", "1", ["resume"], {"resume": body}, {"name": "Jane Doe"}, job)

    folder = dossier_dir("linkedin", "1", "Acme Corp", "Head of IT")
    draft_files = list(folder.glob("*-DRAFT-UNCONFIRMED.docx"))
    assert len(draft_files) == 1


def test_clean_resume_text_writes_to_the_real_filename_as_before(job):
    body = "PROFESSIONAL EXPERIENCE\nLed a team of 12 engineers.\n"
    upsert_application("linkedin", "1", status="under review", resume_text=body)
    sync_workspace_documents("linkedin", "1", ["resume"], {"resume": body}, {"name": "Jane Doe"}, job)

    folder = dossier_dir("linkedin", "1", "Acme Corp", "Head of IT")
    real_path = folder / _workspace_filename("resume_text", "Jane Doe", job)
    assert real_path.exists()
    assert not list(folder.glob("*-DRAFT-UNCONFIRMED.docx"))


def test_an_existing_resolved_real_file_is_not_overwritten_by_a_later_unconfirmed_draft(job):
    # A previously resolved, clean draft's real file must survive even
    # though a NEW regenerate came back with a fresh unresolved guess -
    # never trade good content on disk for unconfirmed content.
    clean_body = "PROFESSIONAL EXPERIENCE\nLed a team of 12 engineers.\n"
    upsert_application("linkedin", "1", status="under review", resume_text=clean_body)
    sync_workspace_documents("linkedin", "1", ["resume"], {"resume": clean_body}, {"name": "Jane Doe"}, job)

    folder = dossier_dir("linkedin", "1", "Acme Corp", "Head of IT")
    real_path = folder / _workspace_filename("resume_text", "Jane Doe", job)
    assert real_path.exists()
    original_bytes = real_path.read_bytes()

    unconfirmed_body = "PROFESSIONAL EXPERIENCE\nLed a team of 8-10 engineers?\n"
    upsert_application("linkedin", "1", status="under review", resume_text=unconfirmed_body)
    sync_workspace_documents("linkedin", "1", ["resume"], {"resume": unconfirmed_body}, {"name": "Jane Doe"}, job)

    assert real_path.read_bytes() == original_bytes
    assert list(folder.glob("*-DRAFT-UNCONFIRMED.docx"))


def test_resolving_deletes_the_stale_draft_unconfirmed_file(job):
    unconfirmed_body = "PROFESSIONAL EXPERIENCE\nLed a team of 8-10 engineers?\n"
    upsert_application("linkedin", "1", status="under review", resume_text=unconfirmed_body)
    sync_workspace_documents("linkedin", "1", ["resume"], {"resume": unconfirmed_body}, {"name": "Jane Doe"}, job)

    folder = dossier_dir("linkedin", "1", "Acme Corp", "Head of IT")
    assert list(folder.glob("*-DRAFT-UNCONFIRMED.docx"))

    resolved_body = "PROFESSIONAL EXPERIENCE\nLed a team of 12 engineers.\n"
    upsert_application("linkedin", "1", status="under review", resume_text=resolved_body)
    sync_workspace_documents("linkedin", "1", ["resume"], {"resume": resolved_body}, {"name": "Jane Doe"}, job)

    assert not list(folder.glob("*-DRAFT-UNCONFIRMED.docx"))
    real_path = folder / _workspace_filename("resume_text", "Jane Doe", job)
    assert real_path.exists()
