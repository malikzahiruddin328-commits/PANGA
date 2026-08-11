"""Real unit tests for the canonical skill taxonomy's deterministic data
layer (2026-08-11) - no AI, no live spend."""

import json

from skills.canonical_taxonomy import add_canonical_entry, find_canonical_id, load_taxonomy, save_taxonomy


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
