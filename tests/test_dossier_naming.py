from tailoring.dossier import (
    _legacy_descriptive_workspace_filename,
    _legacy_hash_suffixed_workspace_filename,
    _MAX_WORKSPACE_FILENAME_LEN,
    _migrate_descriptive_filename,
    _migrate_hash_suffixed_filename,
    _migrate_legacy_filename,
    _slug,
    _workspace_filename,
    dossier_dir,
)


def test_slug_is_stable_and_url_safe():
    slug = _slug("ZipRecruiter", "job-123", "Aerospike", "Head of IT")
    assert slug.startswith("aerospike-head-of-it-")
    assert " " not in slug
    assert slug == _slug("ZipRecruiter", "job-123", "Aerospike", "Head of IT")


def test_slug_differs_by_source_or_job_id_even_with_same_org_title():
    a = _slug("ZipRecruiter", "1", "Aerospike", "Head of IT")
    b = _slug("Dice", "1", "Aerospike", "Head of IT")
    c = _slug("ZipRecruiter", "2", "Aerospike", "Head of IT")
    assert len({a, b, c}) == 3


def test_slug_handles_missing_org_and_title():
    slug = _slug("ZipRecruiter", "job-123", None, None)
    assert slug.startswith("job-")


def test_workspace_filename_drops_title_keeps_name_doctype_org():
    # 2026-08-05 format change: Title is dropped entirely (still preserved
    # in the parent dossier folder name via _slug()) - a real posting title
    # alone could already run past the new 50-char ceiling on its own.
    job = {"source": "Dice", "job_id": "123", "title": "Head of IT", "organization": "Aerospike"}
    filename = _workspace_filename("resume_text", "Zahir Uddin", job)
    assert filename == "Zahir_Uddin_Resume_Aerospike.docx"


def test_workspace_filename_strips_illegal_windows_filename_chars():
    job = {"source": "Dice", "job_id": "123", "title": "IT Director: Data/AI", "organization": "Acme Corp"}
    filename = _workspace_filename("cover_letter_text", "Zahir Uddin", job)
    for char in '<>:"/\\|?*':
        assert char not in filename


def test_workspace_filename_skips_missing_parts():
    job = {"source": "Dice", "job_id": "123", "title": None, "organization": None}
    filename = _workspace_filename("resume_text", "Zahir Uddin", job)
    assert filename == "Zahir_Uddin_Resume.docx"


def test_workspace_filename_stays_under_max_length_with_long_name_and_org():
    job = {
        "source": "Dice", "job_id": "123",
        "title": "Senior Director of IT Enterprise Applications and Global Digital Transformation",
        "organization": "The Extremely Long Multinational Conglomerate Holding Company Group International",
    }
    filename = _workspace_filename("leadership_summary_text", "Muhammed Zahiruddin Khan", job)
    assert len(filename) <= _MAX_WORKSPACE_FILENAME_LEN
    assert filename.endswith(".docx")


def test_workspace_filename_never_truncates_doc_type():
    # DocType is what distinguishes Resume from CoverLetter - must survive
    # intact even when Name/Org both have to give up length to fit.
    job = {
        "source": "Dice", "job_id": "123",
        "organization": "An Extremely Long Organization Name That Keeps Going And Going",
    }
    filename = _workspace_filename("leadership_summary_text", "A Very Long Candidate Full Name Here", job)
    assert "Leadership_summary" in filename


def test_workspace_filename_short_field_is_not_truncated_when_other_is_long():
    # "truncate proportionally if a SINGLE one is unusually long" - a short
    # name shouldn't get clipped just because the organization is huge.
    job = {
        "source": "Dice", "job_id": "123",
        "organization": "An Extremely Long Organization Name That Keeps Going And Going And Going",
    }
    filename = _workspace_filename("resume_text", "Al", job)
    assert filename.startswith("Al_Resume_")
    assert len(filename) <= _MAX_WORKSPACE_FILENAME_LEN


def test_workspace_filename_can_collide_across_different_jobs_same_company_by_design():
    # 2026-08-05: Zahir asked for the disambiguating hash suffix removed
    # (didn't want a trailing hex string on the filename), even though it
    # was originally added to guarantee this exact case stays unique.
    # Accepted tradeoff, documented in _workspace_filename()'s docstring:
    # two different roles at the same company - same candidate, same doc
    # type, same organization - can now produce the identical filename.
    # This is fine WITHIN the app (see the next test - it never actually
    # collides on disk, each job has its own folder) and only matters if
    # Zahir manually gathers files out of two different per-job folders
    # into one flat destination himself (Downloads, an email) - a real but
    # narrow, low-likelihood case not worth adding cross-folder collision-
    # scanning complexity to guard against.
    job_a = {"source": "Dice", "job_id": "role-a", "title": "VP Engineering", "organization": "Acme Corp"}
    job_b = {"source": "Dice", "job_id": "role-b", "title": "VP Product", "organization": "Acme Corp"}
    filename_a = _workspace_filename("resume_text", "Zahir Uddin", job_a)
    filename_b = _workspace_filename("resume_text", "Zahir Uddin", job_b)
    assert filename_a == filename_b


def test_same_filename_never_collides_on_disk_because_folders_differ():
    # The actual guarantee that matters in-app: even though the FILENAME can
    # now be identical across two different jobs at the same company (see
    # above), each job still gets its own physically separate dossier
    # folder (_slug() folds in a per-job hash) - so sync_workspace_documents()
    # never writes two different jobs' documents into the same directory,
    # meaning an in-app overwrite/collision genuinely cannot happen.
    job_a = {"source": "Dice", "job_id": "role-a", "title": "VP Engineering", "organization": "Acme Corp"}
    job_b = {"source": "Dice", "job_id": "role-b", "title": "VP Product", "organization": "Acme Corp"}
    folder_a = dossier_dir(job_a["source"], job_a["job_id"], job_a["organization"], job_a["title"])
    folder_b = dossier_dir(job_b["source"], job_b["job_id"], job_b["organization"], job_b["title"])
    assert folder_a != folder_b


def test_workspace_filename_is_stable_for_same_job():
    job = {"source": "Dice", "job_id": "123", "title": "Head of IT", "organization": "Aerospike"}
    assert _workspace_filename("resume_text", "Zahir Uddin", job) == _workspace_filename("resume_text", "Zahir Uddin", job)


def test_migrate_legacy_filename_renames_when_new_name_absent(tmp_path):
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    (folder / "resume.docx").write_text("old content")

    _migrate_legacy_filename(folder, "resume_text", "Zahir_Uddin_Resume_Head_of_IT_Aerospike.docx")

    assert not (folder / "resume.docx").exists()
    assert (folder / "Zahir_Uddin_Resume_Head_of_IT_Aerospike.docx").read_text() == "old content"


def test_migrate_legacy_filename_does_not_overwrite_existing_new_file(tmp_path):
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    (folder / "resume.docx").write_text("stale legacy copy")
    (folder / "Zahir_Uddin_Resume_Head_of_IT_Aerospike.docx").write_text("current real copy")

    _migrate_legacy_filename(folder, "resume_text", "Zahir_Uddin_Resume_Head_of_IT_Aerospike.docx")

    # Legacy file untouched (not deleted), new file's content is untouched -
    # never silently clobbers a real edited copy.
    assert (folder / "resume.docx").read_text() == "stale legacy copy"
    assert (folder / "Zahir_Uddin_Resume_Head_of_IT_Aerospike.docx").read_text() == "current real copy"


def test_migrate_legacy_filename_no_op_when_no_legacy_file(tmp_path):
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    _migrate_legacy_filename(folder, "resume_text", "Zahir_Uddin_Resume_Head_of_IT_Aerospike.docx")
    assert list(folder.iterdir()) == []


def test_migrate_legacy_filename_no_op_for_field_with_no_legacy_mapping(tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    # apply_answers_text has no legacy filename entry - must not raise.
    _migrate_legacy_filename(folder, "apply_answers_text", "whatever.docx")


def test_migrate_descriptive_filename_renames_when_new_name_absent(tmp_path):
    # A workspace folder created under the 2026-07-31 through 2026-08-05
    # {Name}_{DocType}_{Title}_{Organization}.docx format - the 50-char
    # ceiling didn't exist yet, so this format is now itself "legacy" and
    # must migrate forward without orphaning Zahir's real edits.
    job = {"source": "Dice", "job_id": "123", "title": "Head of IT", "organization": "Aerospike"}
    old_name = _legacy_descriptive_workspace_filename("resume_text", "Zahir Uddin", job)
    new_name = _workspace_filename("resume_text", "Zahir Uddin", job)
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    (folder / old_name).write_text("Zahir's real edited content")

    _migrate_descriptive_filename(folder, "resume_text", new_name, "Zahir Uddin", job)

    assert not (folder / old_name).exists()
    assert (folder / new_name).read_text() == "Zahir's real edited content"


def test_migrate_descriptive_filename_does_not_overwrite_existing_new_file(tmp_path):
    job = {"source": "Dice", "job_id": "123", "title": "Head of IT", "organization": "Aerospike"}
    old_name = _legacy_descriptive_workspace_filename("resume_text", "Zahir Uddin", job)
    new_name = _workspace_filename("resume_text", "Zahir Uddin", job)
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    (folder / old_name).write_text("stale descriptive-format copy")
    (folder / new_name).write_text("current real copy")

    _migrate_descriptive_filename(folder, "resume_text", new_name, "Zahir Uddin", job)

    assert (folder / old_name).read_text() == "stale descriptive-format copy"
    assert (folder / new_name).read_text() == "current real copy"


def test_migrate_descriptive_filename_no_op_when_no_old_file(tmp_path):
    job = {"source": "Dice", "job_id": "123", "title": "Head of IT", "organization": "Aerospike"}
    new_name = _workspace_filename("resume_text", "Zahir Uddin", job)
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    _migrate_descriptive_filename(folder, "resume_text", new_name, "Zahir Uddin", job)
    assert list(folder.iterdir()) == []


def test_migrate_descriptive_filename_no_op_when_old_and_new_names_coincide(tmp_path):
    # Defensive: if the old and new formats ever happened to compute the
    # same name for some field, renaming a file onto itself must not raise
    # (Path.rename onto an identical existing path is undefined on some
    # platforms) - guarded explicitly in _migrate_descriptive_filename.
    job = {"source": "Dice", "job_id": "123", "title": "Head of IT", "organization": "Aerospike"}
    new_name = _workspace_filename("resume_text", "Zahir Uddin", job)
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    (folder / new_name).write_text("already current")
    _migrate_descriptive_filename(folder, "resume_text", new_name, "Zahir Uddin", job)
    assert (folder / new_name).read_text() == "already current"


def test_migrate_hash_suffixed_filename_renames_when_new_name_absent(tmp_path):
    # A workspace folder created under the brief 2026-08-05 hash-suffixed
    # format (commit 51120a7) - superseded almost immediately when Zahir
    # asked for the trailing hex string removed, but real edits made in
    # that brief window must still migrate forward, not get orphaned.
    job = {"source": "Dice", "job_id": "123", "title": "Head of IT", "organization": "Aerospike"}
    old_name = _legacy_hash_suffixed_workspace_filename("resume_text", "Zahir Uddin", job)
    new_name = _workspace_filename("resume_text", "Zahir Uddin", job)
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    (folder / old_name).write_text("Zahir's real edited content")

    _migrate_hash_suffixed_filename(folder, "resume_text", new_name, "Zahir Uddin", job)

    assert not (folder / old_name).exists()
    assert (folder / new_name).read_text() == "Zahir's real edited content"


def test_migrate_hash_suffixed_filename_does_not_overwrite_existing_new_file(tmp_path):
    job = {"source": "Dice", "job_id": "123", "title": "Head of IT", "organization": "Aerospike"}
    old_name = _legacy_hash_suffixed_workspace_filename("resume_text", "Zahir Uddin", job)
    new_name = _workspace_filename("resume_text", "Zahir Uddin", job)
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    (folder / old_name).write_text("stale hash-suffixed copy")
    (folder / new_name).write_text("current real copy")

    _migrate_hash_suffixed_filename(folder, "resume_text", new_name, "Zahir Uddin", job)

    assert (folder / old_name).read_text() == "stale hash-suffixed copy"
    assert (folder / new_name).read_text() == "current real copy"


def test_migrate_hash_suffixed_filename_no_op_when_no_old_file(tmp_path):
    job = {"source": "Dice", "job_id": "123", "title": "Head of IT", "organization": "Aerospike"}
    new_name = _workspace_filename("resume_text", "Zahir Uddin", job)
    folder = tmp_path / "aerospike-head-of-it-abc123"
    folder.mkdir()
    _migrate_hash_suffixed_filename(folder, "resume_text", new_name, "Zahir Uddin", job)
    assert list(folder.iterdir()) == []
