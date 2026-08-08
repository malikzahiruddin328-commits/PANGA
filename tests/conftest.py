"""Shared fixtures for Panga's regression suite.

Every store module (job_store.py, applications.py, target_accounts.py,
dossier.py, ...) hardcodes its own data-file path under data/ at import
time. `isolated_data` monkeypatches every one of those path constants to a
location under pytest's own tmp_path for the duration of a test, so tests
can freely create/update/delete records without ever touching the real
data/ folder - the project has a documented history of a near-data-loss
incident from careless writes to the real stores, so tests must not be
able to repeat that by construction, not just by convention.

Real AES-256-GCM encryption (security/crypto_store.py) is used as-is
against these tmp_path files - it needs no mocking, and exercising the
real encrypt/decrypt round-trip is itself worth having in the suite.
"""

import pytest


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    """Redirects every store this suite touches to tmp_path. Import the
    module fresh inside each test (or via the fixtures below) so the
    patched path constant is the one actually read at call time - these
    modules read their PATH constant as a module-level global, not per-call,
    so patch before first use in the test.
    """
    import search.job_store as job_store
    import tailoring.applications as applications
    import prospector.target_accounts as target_accounts
    import tailoring.dossier as dossier
    import tailoring.interview_prep as interview_prep
    import tailoring.cta_emails as cta_emails
    import prospector.outreach as outreach
    import fulfillment
    import profile.storage as profile_storage
    import profile.ingest as profile_ingest
    import cost_log
    import linkedin.storage as linkedin_storage

    monkeypatch.setattr(job_store, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(applications, "APPLICATIONS_PATH", tmp_path / "applications.json")
    monkeypatch.setattr(target_accounts, "TARGET_ACCOUNTS_PATH", tmp_path / "target_accounts.json")
    monkeypatch.setattr(target_accounts, "WEBSITE_LOOKUP_COST_PATH", tmp_path / "website_lookup_cost.json")
    monkeypatch.setattr(dossier, "DOSSIER_DIR", tmp_path / "dossiers")
    monkeypatch.setattr(interview_prep, "INTERVIEW_PREP_PATH", tmp_path / "interview_prep.json")
    monkeypatch.setattr(cta_emails, "CTA_EMAILS_PATH", tmp_path / "cta_emails.json")
    monkeypatch.setattr(outreach, "OUTREACH_PATH", tmp_path / "outreach.json")
    monkeypatch.setattr(fulfillment, "SYNC_STATUS_PATH", tmp_path / "fulfillment_status.json")
    # Real gap found 2026-08-06: profile/storage.py's MASTER_PROFILE_PATH was
    # never added here despite being exactly the kind of store this fixture
    # exists to isolate - one test file (test_dossier_edit_detection.py) had
    # been patching it ad hoc in its own fixture instead. Covered centrally
    # now so every test gets the same safety by construction, not by each
    # test file remembering to do it itself.
    monkeypatch.setattr(profile_storage, "MASTER_PROFILE_PATH", tmp_path / "master_profile.json")
    # Same class of gap as MASTER_PROFILE_PATH above - profile/ingest.py's
    # manifest/raw-text store was never isolated either, so any test that
    # uploads/removes a document would silently touch the real data/profile
    # folder. PROJECT_ROOT must move too: ingest_uploaded_document() computes
    # each entry's "extracted_to" via out_path.relative_to(PROJECT_ROOT), which
    # raises ValueError if OUTPUT_DIR isn't actually under PROJECT_ROOT.
    monkeypatch.setattr(profile_ingest, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(profile_ingest, "OUTPUT_DIR", tmp_path / "profile_raw")
    monkeypatch.setattr(profile_ingest, "MANIFEST_RESULT_PATH", tmp_path / "profile_raw" / "manifest_result.json")
    # score-first-resume-flow spec item 7 (2026-08-08): cost_log.py's
    # running API-cost log is exactly the kind of store this fixture
    # exists to isolate - added centrally so no test that exercises
    # call_structured()/call_with_web_search() can ever write real cost
    # entries into the actual data/ folder.
    monkeypatch.setattr(cost_log, "COST_LOG_PATH", tmp_path / "cost_log.json")
    # Same class of gap as MASTER_PROFILE_PATH/PROJECT_ROOT above - found
    # 2026-08-08 while writing tests for the LinkedIn suggestion-persist
    # fix: linkedin.storage.LINKEDIN_PATH was never isolated either, so any
    # test exercising save_analysis()/mark_suggestion_status() would have
    # silently read/written the real data/linkedin folder (a full LinkedIn
    # profile export - at least as sensitive as the resume text this
    # fixture already protects).
    monkeypatch.setattr(linkedin_storage, "LINKEDIN_PATH", tmp_path / "linkedin_profile.json")
    return tmp_path
