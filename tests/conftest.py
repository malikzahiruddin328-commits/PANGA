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

    monkeypatch.setattr(job_store, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(applications, "APPLICATIONS_PATH", tmp_path / "applications.json")
    monkeypatch.setattr(target_accounts, "TARGET_ACCOUNTS_PATH", tmp_path / "target_accounts.json")
    monkeypatch.setattr(target_accounts, "WEBSITE_LOOKUP_COST_PATH", tmp_path / "website_lookup_cost.json")
    monkeypatch.setattr(dossier, "DOSSIER_DIR", tmp_path / "dossiers")
    monkeypatch.setattr(interview_prep, "INTERVIEW_PREP_PATH", tmp_path / "interview_prep.json")
    monkeypatch.setattr(cta_emails, "CTA_EMAILS_PATH", tmp_path / "cta_emails.json")
    monkeypatch.setattr(outreach, "OUTREACH_PATH", tmp_path / "outreach.json")
    monkeypatch.setattr(fulfillment, "SYNC_STATUS_PATH", tmp_path / "fulfillment_status.json")
    return tmp_path
