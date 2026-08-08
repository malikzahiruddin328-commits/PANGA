from search.job_store import save_jobs
from tailoring.applications import upsert_application
from tailoring.baseline_resume import select_baseline_resume_text


def _job(source, job_id, title, organization, required=None, preferred=None):
    return {
        "source": source, "job_id": job_id, "title": title, "organization": organization,
        "ats_required_keywords": required or [], "ats_preferred_keywords": preferred or [],
    }


def test_falls_back_to_generic_baseline_with_no_past_applications(isolated_data, monkeypatch):
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "resume_text", lambda: "GENERIC RESUME TEXT")
    save_jobs([_job("linkedin", "1", "Director, IT", "Acme", required=["Python", "AWS"])])

    text, label = select_baseline_resume_text({
        "source": "linkedin", "job_id": "1", "ats_required_keywords": ["Python", "AWS"], "ats_preferred_keywords": [],
    })
    assert text == "GENERIC RESUME TEXT"
    assert label == "your general profile resume"


def test_falls_back_to_generic_baseline_when_no_candidate_clears_the_threshold(isolated_data, monkeypatch):
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "resume_text", lambda: "GENERIC RESUME TEXT")
    save_jobs([_job(
        "linkedin", "1", "Marketing Manager", "Acme",
        required=["SEO", "Content Strategy", "Brand Management"],
    )])
    upsert_application("linkedin", "1", status="applied", resume_text="Old marketing resume.")

    target_job = {
        "source": "linkedin", "job_id": "2",
        "ats_required_keywords": ["Python", "AWS", "Kubernetes", "SQL"], "ats_preferred_keywords": [],
    }
    text, label = select_baseline_resume_text(target_job)
    assert text == "GENERIC RESUME TEXT"
    assert label == "your general profile resume"


def test_picks_the_most_similar_past_drafted_resume(isolated_data, monkeypatch):
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "resume_text", lambda: "GENERIC RESUME TEXT")
    save_jobs([
        _job("linkedin", "close", "IT Director", "Beta Pharma", required=["Python", "AWS", "SQL", "Snowflake"]),
        _job("linkedin", "far", "Marketing Manager", "Acme", required=["SEO", "Content Strategy"]),
    ])
    upsert_application("linkedin", "close", status="applied", resume_text="A drafted resume for the close-match job.")
    upsert_application("linkedin", "far", status="applied", resume_text="A drafted resume for the unrelated job.")

    target_job = {
        "source": "linkedin", "job_id": "new",
        "ats_required_keywords": ["Python", "AWS", "SQL", "Kubernetes"], "ats_preferred_keywords": [],
    }
    text, label = select_baseline_resume_text(target_job)
    assert text == "A drafted resume for the close-match job."
    assert "Beta Pharma" in label and "IT Director" in label


def test_ignores_past_applications_with_no_resume_text(isolated_data, monkeypatch):
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "resume_text", lambda: "GENERIC RESUME TEXT")
    save_jobs([_job("linkedin", "1", "IT Director", "Beta Pharma", required=["Python", "AWS", "SQL"])])
    upsert_application("linkedin", "1", status="applied", resume_text="")

    target_job = {
        "source": "linkedin", "job_id": "new",
        "ats_required_keywords": ["Python", "AWS", "SQL"], "ats_preferred_keywords": [],
    }
    text, label = select_baseline_resume_text(target_job)
    assert text == "GENERIC RESUME TEXT"


def test_ignores_past_applications_whose_job_record_has_no_cached_keywords(isolated_data, monkeypatch):
    # Spec's explicit "no new AI call" constraint - a past job with no
    # cached keyword list is simply excluded from the comparison pool,
    # never backfilled here.
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "resume_text", lambda: "GENERIC RESUME TEXT")
    save_jobs([{"source": "linkedin", "job_id": "1", "title": "IT Director", "organization": "Beta Pharma"}])
    upsert_application("linkedin", "1", status="applied", resume_text="A drafted resume with no cached keywords on its job.")

    target_job = {
        "source": "linkedin", "job_id": "new",
        "ats_required_keywords": ["Python", "AWS", "SQL"], "ats_preferred_keywords": [],
    }
    text, label = select_baseline_resume_text(target_job)
    assert text == "GENERIC RESUME TEXT"


def test_either_or_group_members_count_toward_overlap(isolated_data, monkeypatch):
    # Item 2's either/or groups - deliberately coarse here (every
    # alternative counts), since this is picking a topically-similar
    # resume, not doing precise literal-match scoring.
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "resume_text", lambda: "GENERIC RESUME TEXT")
    save_jobs([_job(
        "linkedin", "1", "IT Director", "Beta Pharma",
        required=[{"any_of": ["Master's degree", "Bachelor's degree"]}, "Python", "AWS", "SQL"],
    )])
    upsert_application("linkedin", "1", status="applied", resume_text="A drafted resume for the degree-group job.")

    target_job = {
        "source": "linkedin", "job_id": "new",
        "ats_required_keywords": [{"any_of": ["Master's degree", "Bachelor's degree"]}, "Python", "AWS", "SQL"],
        "ats_preferred_keywords": [],
    }
    text, label = select_baseline_resume_text(target_job)
    assert text == "A drafted resume for the degree-group job."


def test_excludes_the_target_jobs_own_existing_application(isolated_data, monkeypatch):
    # Real gap found live 2026-08-08: if the target job itself already has
    # a drafted resume (Step 1 is only meant to run before one exists, but
    # nothing stops a caller from invoking this on one that already has
    # one), it would trivially "match" itself at overlap 1.0 and hand back
    # its own current resume as its own baseline.
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "resume_text", lambda: "GENERIC RESUME TEXT")
    save_jobs([_job("linkedin", "self", "IT Director", "Beta Pharma", required=["Python", "AWS", "SQL"])])
    upsert_application("linkedin", "self", status="applied", resume_text="My own already-drafted resume.")

    target_job = {
        "source": "linkedin", "job_id": "self",
        "ats_required_keywords": ["Python", "AWS", "SQL"], "ats_preferred_keywords": [],
    }
    text, label = select_baseline_resume_text(target_job)
    assert text == "GENERIC RESUME TEXT"
    assert label == "your general profile resume"


def test_target_job_with_no_cached_keywords_falls_back_to_generic(isolated_data, monkeypatch):
    import profile.ingest as ingest

    monkeypatch.setattr(ingest, "resume_text", lambda: "GENERIC RESUME TEXT")
    text, label = select_baseline_resume_text({"source": "linkedin", "job_id": "new"})
    assert text == "GENERIC RESUME TEXT"
    assert label == "your general profile resume"
