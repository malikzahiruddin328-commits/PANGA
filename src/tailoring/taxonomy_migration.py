"""One-time (plus future re-runs as the label pool grows) AI-powered
clustering pass that proposes an initial canonical taxonomy from a real
pool of existing free-text skill labels (2026-08-11).

Deliberately separate from skills/canonical_taxonomy.py (which stays
pure/deterministic, zero AI dependency) - this is the one genuinely AI-
powered piece, kept in tailoring/ alongside this app's other reasoning
calls, dependency-injected (call_structured_fn/client) the same way
tailoring.resume_diagnosis.diagnose_resume_gaps() is, for the same
reason: trivially mockable in tests, no live spend required to verify
the wiring/logic.

Safety design (2026-08-11, Zahir's explicit "nothing gets lost" bar):
this module's clustering call is best-effort AI value-add for grouping
near-duplicates - it is NOT the thing that guarantees no label is lost.
migrate_gap_interview_answers() below guarantees full coverage
deterministically: every existing answer's skill label is checked against
the proposed taxonomy via canonical_taxonomy.find_canonical_id() first,
and if the clustering call somehow missed it (dropped it, mis-clustered
it, or simply never saw it because the call failed/was truncated), it
falls back to canonical_taxonomy.add_canonical_entry() to create its own
canonical entry rather than leaving it unmapped. Worst case, imperfect
clustering means slightly less dedup value than ideal - it can never mean
a real answer silently disappears."""

from llm_client import LLMResponseTruncated
from skills.canonical_taxonomy import add_canonical_entry, find_canonical_id

_CLUSTER_SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "A short, real category name for a group of related skill/experience concepts (e.g. 'ERP & Platform Implementation', 'Regulatory & Compliance', 'Salesforce Ecosystem').",
                    },
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "canonical_label": {
                                    "type": "string",
                                    "description": "A clear, specific label for ONE real underlying skill/experience concept - not a vague umbrella term.",
                                },
                                "aliases": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Every input label from the provided list that refers to this SAME real concept, copied VERBATIM (exact string, not paraphrased) - including the canonical_label's own closest match if it came from the input list.",
                                },
                            },
                            "required": ["canonical_label", "aliases"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["category", "entries"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["categories"],
    "additionalProperties": False,
}

CLUSTER_SYSTEM_PROMPT = """You are organizing a real list of skill/experience labels that were independently generated across many separate rounds by an AI assistant helping one job candidate - the same real-world fact was often worded differently each time (e.g. "CSAT/NPS numeric scores on consulting engagements" vs "Customer satisfaction scores on consulting engagements" - same real concept, no shared vocabulary).

Group the ENTIRE input list into canonical entries under sensible categories. Every single input label must appear as an alias of EXACTLY ONE canonical entry - none dropped, none duplicated across two entries. If a label is genuinely distinct from everything else (no real duplicate in the list), it still gets its own canonical entry with itself as its only alias - never discard a label for not matching anything.

Group generously when two labels plainly refer to the same real underlying fact, even with very different wording or abbreviations - but do NOT merge genuinely different facts just because they share a topic (e.g. "SAP S/4HANA implementation cost" and "SAP S/4HANA go-live date" are the same real fact if they're asking about the same project; "SS&C platform experience" and "SEI platform experience" are DIFFERENT facts about different vendors, even though both are asset-management platforms - do not merge those).

Return the full grouped structure via the structured output schema."""


def cluster_labels_into_taxonomy(labels: list[str], call_structured_fn, client, model: str | None = None) -> dict:
    """Real AI clustering pass. Returns a taxonomy dict in the same shape
    canonical_taxonomy.load_taxonomy()/save_taxonomy() use - caller is
    responsible for validating coverage (see migrate_gap_interview_answers)
    and persisting via save_taxonomy()."""
    unique_labels = sorted(set(l for l in labels if l))
    # Real truncation hit live (2026-08-11) at max_tokens=16000 on the real
    # 452-label pool - every input label has to be reproduced verbatim as
    # an alias somewhere in the output, so total output size scales with
    # the pool itself, not a fixed small classification response. Escalates
    # on a genuine LLMResponseTruncated, same pattern as _draft_one's own
    # max_tokens_tiers - this call will need to run again as the taxonomy
    # grows (Zahir's explicit "real headroom for growth" ask), so a single
    # fixed ceiling that happened to work once isn't a real fix.
    max_tokens_tiers = [16000, 32000, 48000]
    for max_tokens in max_tokens_tiers:
        try:
            data = call_structured_fn(
                client, system=[{"type": "text", "text": CLUSTER_SYSTEM_PROMPT}],
                user_content=[{"type": "text", "text": "LABELS TO GROUP:\n" + "\n".join(f"- {l}" for l in unique_labels)}],
                schema=_CLUSTER_SCHEMA, max_tokens=max_tokens, model=model, effort="high",
                refusal_message="Claude declined to cluster these labels. Try again.",
                purpose="cluster_skill_taxonomy",
            )
        except LLMResponseTruncated:
            if max_tokens == max_tokens_tiers[-1]:
                raise
            continue
        break

    taxonomy = {"_meta": {
        "description": "Fixed, growing taxonomy of canonical skill/experience identities - see canonical_skills.json's own _meta for the full explanation.",
    }}
    for group in data.get("categories", []):
        category = group.get("category") or "Uncategorized"
        for entry in group.get("entries", []):
            canonical_label = entry.get("canonical_label")
            if not canonical_label:
                continue
            add_canonical_entry(taxonomy, category, canonical_label, aliases=entry.get("aliases", []))
    return taxonomy


def migrate_gap_interview_answers(profile: dict, taxonomy: dict) -> tuple[dict, list[str]]:
    """Assigns canonical_skill_id to every entry in profile's real
    gap_interview_answers - NON-destructively (the original free-text
    `skill` field is never removed or overwritten). Guarantees every
    answer gets a real canonical_skill_id: if the taxonomy (from
    clustering) doesn't already cover a given answer's skill label,
    creates a fresh canonical entry for it on the spot via
    add_canonical_entry() rather than leaving it unmapped - "nothing
    gets lost" is enforced here, not assumed from clustering quality.

    Mutates `taxonomy` in place (may add fallback entries) and returns a
    NEW profile dict (does not mutate the input) plus a list of skill
    labels that needed a fallback entry (for reporting - a real signal
    of how much the clustering pass actually covered vs. missed)."""
    import copy

    new_profile = copy.deepcopy(profile)
    fallback_labels = []
    for answer in new_profile.get("gap_interview_answers", []):
        skill = answer.get("skill", "")
        canonical_id = find_canonical_id(skill, taxonomy)
        if canonical_id is None:
            canonical_id = add_canonical_entry(taxonomy, "Uncategorized (migration fallback)", skill, aliases=[])
            fallback_labels.append(skill)
        answer["canonical_skill_id"] = canonical_id
    return new_profile, fallback_labels
