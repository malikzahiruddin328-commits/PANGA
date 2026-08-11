"""Real tests for the taxonomy migration/clustering logic (2026-08-11) -
mocked call_structured, zero live spend. The core non-negotiable property
under test: every real gap_interview_answers entry gets a canonical_skill_id,
with NO exceptions, even when the clustering call does a poor or partial
job - "nothing gets lost" is a deterministic guarantee, not a hope."""

from skills.canonical_taxonomy import find_canonical_id
from tailoring.taxonomy_migration import cluster_labels_into_taxonomy, migrate_gap_interview_answers


def test_cluster_labels_groups_real_near_duplicates_under_one_canonical_entry():
    def _fake_call_structured(client, **kwargs):
        return {
            "categories": [
                {
                    "category": "ERP & Platform Implementation",
                    "entries": [
                        {
                            "canonical_label": "SAP S/4HANA implementation (cost, timeline, go-live)",
                            "aliases": ["SAP S/4HANA implementation cost and go-live", "SAP S/4HANA go-live date and cost"],
                        },
                    ],
                },
            ],
        }

    taxonomy = cluster_labels_into_taxonomy(
        ["SAP S/4HANA implementation cost and go-live", "SAP S/4HANA go-live date and cost"],
        call_structured_fn=_fake_call_structured, client=object(),
    )

    assert find_canonical_id("SAP S/4HANA implementation cost and go-live", taxonomy) == find_canonical_id(
        "SAP S/4HANA go-live date and cost", taxonomy
    )


def test_migrate_gap_interview_answers_assigns_every_answer_a_canonical_id():
    taxonomy = {"_meta": {}, "ERP": [{
        "id": "sap_s4hana", "canonical_label": "SAP S/4HANA implementation",
        "aliases": ["SAP S/4HANA implementation cost and go-live"],
    }]}
    profile = {"gap_interview_answers": [
        {"skill": "SAP S/4HANA implementation cost and go-live", "answer": "18 months, $4M"},
        {"skill": "Never seen before label", "answer": "some answer"},
    ]}

    new_profile, fallback_labels = migrate_gap_interview_answers(profile, taxonomy)

    for answer in new_profile["gap_interview_answers"]:
        assert answer.get("canonical_skill_id")  # every single one gets a real id, no exceptions
    assert fallback_labels == ["Never seen before label"]  # the uncovered one got a fresh fallback entry, not dropped


def test_migrate_gap_interview_answers_never_touches_the_original_skill_field():
    # Non-destructive per Zahir's explicit ask - the original free-text
    # label must survive exactly as it was, never overwritten.
    taxonomy = {"_meta": {}}
    profile = {"gap_interview_answers": [{"skill": "Original free-text label", "answer": "x"}]}

    new_profile, _ = migrate_gap_interview_answers(profile, taxonomy)

    assert new_profile["gap_interview_answers"][0]["skill"] == "Original free-text label"


def test_migrate_gap_interview_answers_does_not_mutate_the_input_profile():
    taxonomy = {"_meta": {}}
    profile = {"gap_interview_answers": [{"skill": "Some label", "answer": "x"}]}

    migrate_gap_interview_answers(profile, taxonomy)

    assert "canonical_skill_id" not in profile["gap_interview_answers"][0]  # original dict left untouched


def test_migrate_handles_every_answer_even_when_clustering_produced_an_empty_taxonomy():
    # Worst-case clustering failure (empty/near-empty result) must still
    # never lose an answer - full fallback coverage.
    taxonomy = {"_meta": {}}
    profile = {"gap_interview_answers": [{"skill": f"Label {i}", "answer": "x"} for i in range(20)]}

    new_profile, fallback_labels = migrate_gap_interview_answers(profile, taxonomy)

    assert len(fallback_labels) == 20
    assert all(a.get("canonical_skill_id") for a in new_profile["gap_interview_answers"])
    # Every fallback got a genuinely distinct id (no accidental collision across 20 similar labels).
    ids = {a["canonical_skill_id"] for a in new_profile["gap_interview_answers"]}
    assert len(ids) == 20
