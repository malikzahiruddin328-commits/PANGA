from tailoring.dossier import _migrate_legacy_filename, _slug, _workspace_filename


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


def test_workspace_filename_matches_old_download_button_convention():
    job = {"title": "Head of IT", "organization": "Aerospike"}
    filename = _workspace_filename("resume_text", "Zahir Uddin", job)
    assert filename == "Zahir_Uddin_Resume_Head_of_IT_Aerospike.docx"


def test_workspace_filename_strips_illegal_windows_filename_chars():
    job = {"title": "IT Director: Data/AI", "organization": "Acme Corp"}
    filename = _workspace_filename("cover_letter_text", "Zahir Uddin", job)
    for char in '<>:"/\\|?*':
        assert char not in filename


def test_workspace_filename_skips_missing_parts():
    job = {"title": None, "organization": None}
    filename = _workspace_filename("resume_text", "Zahir Uddin", job)
    assert filename == "Zahir_Uddin_Resume.docx"


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
