"""skills/lookup.py - real behavior added 2026-08-11 while moving
role_skills.json out of git tracking into data/ (gitignored, same as
every other personal-data store). Unlike before (seeded via git on every
checkout), a fresh checkout or worktree genuinely has no file here yet -
load_role_skills() must tolerate that instead of crashing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skills.lookup import load_role_skills, save_role_skills, skills_for  # noqa: E402


def test_load_role_skills_returns_empty_dict_when_file_does_not_exist(isolated_data):
    assert load_role_skills() == {}


def test_skills_for_returns_empty_list_when_file_does_not_exist(isolated_data):
    assert skills_for("Lifesciences/Pharma", "Head of IT / CIO") == []


def test_save_then_load_round_trips_real_data(isolated_data):
    entries = [{"skill": "Test skill", "why_it_matters": "Test reason."}]
    save_role_skills("Technology / B2B SaaS", "CTO", entries)

    assert skills_for("Technology / B2B SaaS", "CTO") == entries


def test_save_creates_the_data_directory_if_missing(isolated_data):
    import skills.lookup as skills_lookup

    assert not skills_lookup.DATA_PATH.parent.exists() or not skills_lookup.DATA_PATH.exists()
    save_role_skills("Technology / B2B SaaS", "CTO", [{"skill": "x", "why_it_matters": "y"}])
    assert skills_lookup.DATA_PATH.exists()
