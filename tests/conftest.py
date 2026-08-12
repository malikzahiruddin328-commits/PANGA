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


@pytest.fixture(autouse=True)
def _no_real_anthropic_api_calls(monkeypatch):
    """Real gap found live 2026-08-09 (RM, running the full suite from the
    root checkout - the normal environment for any merge check, with a
    real .env/API key present): two AppTest-based UI tests in
    test_results_tab_gap_questions.py mutated resume_text mid-test
    without keeping the new auto-fire feature's resume_gap_scan_
    fingerprint in sync, so llm_client.is_configured() being True (a real
    key present) let the auto-fire's request_additional_gap_questions()
    actually reach the network - a genuine, non-deterministic live API
    call mid-test, silently corrupting both tests' assertions and
    burning real cost. It only went unnoticed in ATS Engine's own
    worktree because that worktree has no .env at all, so
    is_configured() was accidentally False there and the call harmlessly
    short-circuited into a caught DraftingNotConfigured - the worktree
    environment was masking the bug, not proving its absence.

    Unsetting ANTHROPIC_API_KEY for every test, unconditionally, closes
    the entire class of gap at its root rather than chasing individual
    fixtures that forget to keep a fingerprint/mock in sync (that will
    keep happening as new tests get added) - no test in this suite
    references ANTHROPIC_API_KEY or relies on is_configured() returning
    True, so this has zero effect on any test's actual behavior other
    than guaranteeing DraftingNotConfigured (not a real network call)
    for anything that reaches llm_client.get_client() unmocked. Tests
    that DO want to exercise a specific mocked outcome (most of
    test_drafting.py) already bypass this entirely by mocking _client()/
    call_structured()/the target function directly, so they're
    unaffected either way. Runs before isolated_data below (declared
    first, and autouse fixtures with no dependency between them resolve
    in declaration order) so a real .env's key is never visible during
    any test's setup or body."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


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
    import debug_log
    import linkedin.storage as linkedin_storage
    import linkedin.connections_store as connections_store
    import prospector.prospector_score as prospector_score
    import scan_retry_tracker
    import search.freshness_check as freshness_check
    import tailoring.fit_score_prefilter as fit_score_prefilter
    import skills.canonical_taxonomy as canonical_taxonomy
    import skills.lookup as skills_lookup
    import search.exclusion_filter as exclusion_filter

    monkeypatch.setattr(job_store, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(applications, "APPLICATIONS_PATH", tmp_path / "applications.json")
    monkeypatch.setattr(target_accounts, "TARGET_ACCOUNTS_PATH", tmp_path / "target_accounts.json")
    monkeypatch.setattr(target_accounts, "WEBSITE_LOOKUP_COST_PATH", tmp_path / "website_lookup_cost.json")
    monkeypatch.setattr(dossier, "DOSSIER_DIR", tmp_path / "dossiers")
    monkeypatch.setattr(interview_prep, "INTERVIEW_PREP_PATH", tmp_path / "interview_prep.json")
    monkeypatch.setattr(cta_emails, "CTA_EMAILS_PATH", tmp_path / "cta_emails.json")
    monkeypatch.setattr(outreach, "OUTREACH_PATH", tmp_path / "outreach.json")
    monkeypatch.setattr(fulfillment, "SYNC_STATUS_PATH", tmp_path / "fulfillment_status.json")
    # HUB_INBOX_DIR (2026-08-11): get_last_synced_at() now also reads the
    # live scheduled task's hub-inbox report timestamps - isolate this too,
    # same reasoning as every other path here, so no test can read (or,
    # once a test writes a fake report, pollute) the real ~/.claude/hub-inbox.
    monkeypatch.setattr(fulfillment, "HUB_INBOX_DIR", tmp_path / "hub-inbox")
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
    # Same class of gap as LINKEDIN_PATH immediately above, found live
    # 2026-08-10 while writing tests for the file-locking fix on this same
    # module's sibling: linkedin.connections_store.CONNECTIONS_PATH was
    # never isolated either, so any test exercising save_connections()
    # would have silently written the real data/linkedin/connections.json
    # - PII about Zahir's real professional contacts, not just his own
    # data.
    monkeypatch.setattr(connections_store, "CONNECTIONS_PATH", tmp_path / "connections.json")
    # debug_log.py (2026-08-09's always-on error logging addition) writes
    # to a real file path too - isolated for the same reason as every
    # store above, so no test exercising it can touch the real
    # data/logs/panga_debug.log.
    monkeypatch.setattr(debug_log, "LOG_PATH", tmp_path / "panga_debug.log")
    # Same class of gap as MASTER_PROFILE_PATH/LINKEDIN_PATH above - found
    # 2026-08-10 while writing tests for the $-as-LaTeX escaping sweep:
    # prospector.prospector_score.SCORE_PATH was never isolated either, so
    # any test exercising save_prospector_score() would have silently
    # written the real data/prospector/prospector_score.json.
    monkeypatch.setattr(prospector_score, "SCORE_PATH", tmp_path / "prospector_score.json")
    # scan_retry_tracker.py (2026-08-09, the mark-reviewed-on-every-path
    # fix) writes one JSON file per scan name under this directory -
    # isolated for the same reason as every store above.
    monkeypatch.setattr(scan_retry_tracker, "RETRY_DIR", tmp_path / "scan_retries")
    # freshness_check.py's own small state store (2026-08-11, the closed-
    # posting recheck/two-observation fix) - same class of gap as every
    # store above, isolated here so a test exercising
    # check_and_mark_closed_postings() can't touch the real
    # data/jobs/freshness_check_state.json.
    monkeypatch.setattr(freshness_check, "_STATE_PATH", tmp_path / "freshness_check_state.json")
    # fit_score_prefilter.py (2026-08-11, the pre-filter build) - same class
    # of gap as every store above, isolated so a test exercising
    # log_prefilter_skip() can't touch the real
    # data/jobs/prefilter_log.json spot-check log.
    monkeypatch.setattr(fit_score_prefilter, "PREFILTER_LOG_PATH", tmp_path / "prefilter_log.json")
    # canonical_taxonomy.py (2026-08-11, the skill-taxonomy build) - this
    # store is now genuinely mutable (profile/interview.py's save_answer()
    # writes to it), same class of gap as every store above - isolated so
    # a test exercising save_answer()/_canonical_id_for() can't touch the
    # real src/skills/canonical_skills.json.
    monkeypatch.setattr(canonical_taxonomy, "TAXONOMY_PATH", tmp_path / "canonical_skills.json")
    # skills/lookup.py's role_skills.json (2026-08-11, moved under data/
    # alongside canonical_skills.json - both are real personal data, not
    # product code) - same class of gap: this store just became genuinely
    # mutable too (save_role_skills(), used tonight to add a real
    # industry checklist live), isolated so a test exercising
    # skills_for()/save_role_skills() can't touch the real
    # data/skills/role_skills.json.
    monkeypatch.setattr(skills_lookup, "DATA_PATH", tmp_path / "role_skills.json")
    # exclusion_filter.py (search-time title-exclusion build, present as
    # uncommitted work in .claude/worktrees/agent-a872283f45cabffbe as of
    # this addition) - same class of gap as PREFILTER_LOG_PATH above,
    # isolated so a test exercising log_exclusions()/list_exclusions()
    # can't touch the real data/jobs/search_exclusion_log.json.
    monkeypatch.setattr(exclusion_filter, "EXCLUSION_LOG_PATH", tmp_path / "search_exclusion_log.json")
    return tmp_path
