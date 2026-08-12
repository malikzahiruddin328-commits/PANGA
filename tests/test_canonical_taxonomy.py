"""Real unit tests for the canonical skill taxonomy's deterministic data
layer (2026-08-11) - no AI, no live spend."""

import json

import pytest

from skills.canonical_taxonomy import (
    add_canonical_entry,
    find_canonical_id,
    load_taxonomy,
    log_taxonomy_merge,
    merge_canonical_entries,
    save_taxonomy,
)


def _empty_taxonomy():
    return {"_meta": {}}


def test_find_canonical_id_matches_canonical_label_itself():
    taxonomy = {"ERP": [{"id": "sap_s4hana", "canonical_label": "SAP S/4HANA implementation", "aliases": []}]}
    assert find_canonical_id("SAP S/4HANA implementation", taxonomy) == "sap_s4hana"


def test_find_canonical_id_matches_a_stored_alias():
    taxonomy = {"ERP": [{
        "id": "sap_s4hana", "canonical_label": "SAP S/4HANA implementation (cost, timeline, go-live)",
        "aliases": ["SAP S/4HANA go-live date and cost", "ERP (SAP S/4HANA) implementation timeline and cost"],
    }]}
    assert find_canonical_id("SAP S/4HANA go-live date and cost", taxonomy) == "sap_s4hana"


def test_find_canonical_id_returns_none_for_a_genuinely_new_concept():
    taxonomy = {"ERP": [{"id": "sap_s4hana", "canonical_label": "SAP S/4HANA implementation", "aliases": []}]}
    assert find_canonical_id("Workday HCM ownership", taxonomy) is None


def test_add_canonical_entry_creates_a_new_entry_when_nothing_matches():
    taxonomy = _empty_taxonomy()
    new_id = add_canonical_entry(taxonomy, "ERP", "SAP S/4HANA implementation", aliases=["SAP go-live"])

    assert taxonomy["ERP"][0]["id"] == new_id
    assert taxonomy["ERP"][0]["canonical_label"] == "SAP S/4HANA implementation"
    assert taxonomy["ERP"][0]["aliases"] == ["SAP go-live"]


def test_add_canonical_entry_merges_into_an_existing_match_instead_of_duplicating():
    # Real failure mode this must prevent: two different rounds independently
    # propose "the same" concept worded differently - the second one must
    # merge, not create a second entry that drifts right back into the
    # original free-text problem. Uses a pair skills_match() actually
    # catches (confirmed live 2026-08-11 during the real taxonomy audit) -
    # skills_match()'s own real ceiling (misses different-wording pairs
    # like "SAP S/4HANA implementation" vs "SAP S/4HANA go-live date and
    # cost") is exactly why this is a safety net, not the sole mechanism -
    # the AI-driven initial clustering pass is what has to catch those.
    taxonomy = {"CRM": [{"id": "sfdc_integration", "canonical_label": "Salesforce integration architecture", "aliases": []}]}

    result_id = add_canonical_entry(taxonomy, "CRM", "Salesforce integration architecture detail", aliases=["SFDC integration depth"])

    assert result_id == "sfdc_integration"  # reused the existing entry, no new one created
    assert len(taxonomy["CRM"]) == 1
    assert "Salesforce integration architecture detail" in taxonomy["CRM"][0]["aliases"]
    assert "SFDC integration depth" in taxonomy["CRM"][0]["aliases"]


def test_slugify_generates_unique_ids_on_a_genuine_collision():
    from skills.canonical_taxonomy import _slugify

    id1 = _slugify("Data Governance!!", set())
    id2 = _slugify("data-governance", {id1})  # normalizes to the same base slug as id1

    assert id1 != id2  # no silent id collision even when two labels slugify identically


def test_save_and_load_taxonomy_round_trips_via_real_file_io(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    fake_path = tmp_path / "canonical_skills.json"
    monkeypatch.setattr(ct, "TAXONOMY_PATH", fake_path)

    data = {"_meta": {"description": "test"}, "ERP": [{"id": "x", "canonical_label": "X", "aliases": []}]}
    save_taxonomy(data)

    loaded = load_taxonomy()
    assert loaded == data
    assert json.loads(fake_path.read_text(encoding="utf-8")) == data


def test_load_taxonomy_returns_empty_shell_when_file_does_not_exist_yet(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    monkeypatch.setattr(ct, "TAXONOMY_PATH", tmp_path / "does_not_exist.json")
    assert load_taxonomy() == {"_meta": {}}


def test_save_taxonomy_creates_the_data_directory_if_missing(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    fake_path = tmp_path / "skills" / "canonical_skills.json"  # parent dir does not exist yet
    monkeypatch.setattr(ct, "TAXONOMY_PATH", fake_path)
    assert not fake_path.parent.exists()

    save_taxonomy({"_meta": {}})

    assert fake_path.exists()


def _taxonomy_with_two_real_entries():
    # Real pair from tonight's actual taxonomy (2026-08-11) - genuinely the
    # same fact worded two different ways, the exact class of duplicate
    # this whole system exists to catch.
    return {
        "_meta": {},
        "Compliance": [{
            "id": "csat_nps_scores_on_consulting_engagements",
            "canonical_label": "CSAT/NPS scores on consulting engagements",
            "aliases": ["CSAT/NPS numeric scores on consulting engagements"],
        }],
        "Uncategorized": [{
            "id": "customer_satisfaction_scores_on_consulting_engagements",
            "canonical_label": "Customer satisfaction scores on consulting engagements",
            "aliases": [],
        }],
    }


def test_merge_canonical_entries_folds_label_and_aliases_into_survivor(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    monkeypatch.setattr(ct, "TAXONOMY_PATH", tmp_path / "canonical_skills.json")
    save_taxonomy(_taxonomy_with_two_real_entries())

    result = merge_canonical_entries(
        "csat_nps_scores_on_consulting_engagements",
        "customer_satisfaction_scores_on_consulting_engagements",
    )

    survivor = next(e for e in result["Compliance"] if e["id"] == "csat_nps_scores_on_consulting_engagements")
    assert "Customer satisfaction scores on consulting engagements" in survivor["aliases"]
    assert "CSAT/NPS numeric scores on consulting engagements" in survivor["aliases"]  # original alias preserved


def test_merge_canonical_entries_removes_the_merged_away_entry_entirely(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    monkeypatch.setattr(ct, "TAXONOMY_PATH", tmp_path / "canonical_skills.json")
    save_taxonomy(_taxonomy_with_two_real_entries())

    merge_canonical_entries(
        "csat_nps_scores_on_consulting_engagements",
        "customer_satisfaction_scores_on_consulting_engagements",
    )

    reloaded = load_taxonomy()
    all_ids = {e["id"] for entries in reloaded.values() if isinstance(entries, list) for e in entries}
    assert "customer_satisfaction_scores_on_consulting_engagements" not in all_ids
    assert reloaded["Uncategorized"] == []  # entry removed, category left empty rather than deleted


def test_merge_canonical_entries_rejects_merging_an_id_into_itself(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    monkeypatch.setattr(ct, "TAXONOMY_PATH", tmp_path / "canonical_skills.json")
    save_taxonomy(_taxonomy_with_two_real_entries())

    with pytest.raises(ValueError):
        merge_canonical_entries("csat_nps_scores_on_consulting_engagements", "csat_nps_scores_on_consulting_engagements")


def test_merge_canonical_entries_rejects_a_survivor_id_that_does_not_exist(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    monkeypatch.setattr(ct, "TAXONOMY_PATH", tmp_path / "canonical_skills.json")
    save_taxonomy(_taxonomy_with_two_real_entries())

    with pytest.raises(ValueError):
        merge_canonical_entries("does_not_exist", "customer_satisfaction_scores_on_consulting_engagements")


def test_merge_canonical_entries_rejects_a_merged_away_id_that_does_not_exist(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    monkeypatch.setattr(ct, "TAXONOMY_PATH", tmp_path / "canonical_skills.json")
    save_taxonomy(_taxonomy_with_two_real_entries())

    with pytest.raises(ValueError):
        merge_canonical_entries("csat_nps_scores_on_consulting_engagements", "does_not_exist")


def test_log_taxonomy_merge_appends_a_real_auditable_record(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    log_path = tmp_path / "taxonomy_merge_log.jsonl"
    monkeypatch.setattr(ct, "MERGE_LOG_PATH", log_path)

    log_taxonomy_merge(
        survivor_id="csat_nps_scores_on_consulting_engagements",
        survivor_label="CSAT/NPS scores on consulting engagements",
        merged_away_id="customer_satisfaction_scores_on_consulting_engagements",
        merged_away_label="Customer satisfaction scores on consulting engagements",
        reasoning="Same real fact - customer satisfaction score on a consulting engagement, worded two ways.",
        answers_redirected=1,
        timestamp="2026-08-11T22:00:00Z",
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["survivor_id"] == "csat_nps_scores_on_consulting_engagements"
    assert record["gap_interview_answers_redirected"] == 1


def test_log_taxonomy_merge_appends_rather_than_overwrites_on_a_second_call(tmp_path, monkeypatch):
    import skills.canonical_taxonomy as ct

    log_path = tmp_path / "taxonomy_merge_log.jsonl"
    monkeypatch.setattr(ct, "MERGE_LOG_PATH", log_path)

    for i in range(2):
        log_taxonomy_merge(
            survivor_id=f"survivor_{i}", survivor_label="X", merged_away_id=f"merged_{i}", merged_away_label="Y",
            reasoning="test", answers_redirected=0, timestamp="2026-08-11T22:00:00Z",
        )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
