"""Title-cluster resolution (Feature 2, 2026-08-17): deterministic mapping
from a job's title to a user-configured "title cluster" - a short label
covering a family of related target-role titles Zahir applies to that
genuinely share ~70-80% of the same keyword asks (e.g. CIO, Head of IT, VP
Enterprise Architecture). This exists so tailoring.drafting's keyword-gap
question generation can credit a fact already confirmed for one job in a
cluster against a NEW job in the same cluster, instead of re-asking a
near-duplicate question every time (real confirmed examples: "onshore/
offshore teams", "multi-site operations" re-asked despite already being
answered/evidenced for a same-title-family job).

Deliberately NOT AI-inferred and NOT reusing skill_label_match's fuzzy
skills_match()/normalize_skill_label() machinery - those are built for
matching free-text SKILL labels against each other (word-boundary phrase
containment), not for matching a job TITLE against a short configured
phrase list. Title-cluster matching only ever needs "does this job title
contain one of the configured phrases for this cluster," case-insensitive -
a plain substring check, same simplicity precedent as
exclusion_filter.py's own _custom_exclude() (also case-insensitive
substring, also explicitly NOT the \\b-boundary regex the built-in
exclusion layers use, for the same "a non-technical user's free-form
fragment should just work" reason).

Config lives in config/settings.yaml under "title_clusters" - a list of
{"name": str, "titles": [str, ...]} dicts, same shape family as
"custom_title_exclusions" (see exclusion_filter.load_custom_title_
exclusions()) and edited via the same Settings-tab text-area pattern
(ui/app.py, right next to the custom-exclusions widget).
"""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def load_title_clusters() -> list[dict]:
    """Returns the user's configured title clusters from config/settings.yaml's
    "title_clusters" key - a list of {"name": str, "titles": [str, ...]}
    dicts. Missing file or missing key both resolve to an empty list, not an
    error, same "unused field is a true no-op" convention as
    exclusion_filter.load_custom_title_exclusions()."""
    if not SETTINGS_PATH.exists():
        return []
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    clusters = data.get("title_clusters") or []
    # Defensive against a malformed manual edit (missing "name"/"titles"
    # keys, or "titles" not a list) - skip the malformed entry rather than
    # crashing every caller that touches title-cluster resolution, same
    # "fail toward inert, not toward a crash" posture as this module's
    # sibling exclusion-filter code.
    cleaned = []
    for c in clusters:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        titles = c.get("titles")
        if not name or not isinstance(titles, list):
            continue
        cleaned.append({"name": name, "titles": [str(t) for t in titles if str(t).strip()]})
    return cleaned


def resolve_title_cluster(title: str, clusters: list[dict] | None = None) -> str | None:
    """Returns the name of the first configured cluster whose title list
    contains a case-insensitive substring/word match against `title`, or
    None if no cluster matches. clusters=None (the default) loads the
    current config itself, same "None loads fresh, caller may pass an
    already-loaded list to avoid re-reading settings.yaml per job" pattern
    as exclusion_filter.check_exclusion()'s own custom_exclusions param.

    Checked in configured order - if a title happens to match two clusters
    (overlapping configuration is the user's own choice, not validated
    against), the first one wins, deterministically, every call."""
    if not title:
        return None
    if clusters is None:
        clusters = load_title_clusters()
    title_lower = title.lower()
    for cluster in clusters:
        for phrase in cluster.get("titles") or []:
            phrase_clean = (phrase or "").strip().lower()
            if phrase_clean and phrase_clean in title_lower:
                return cluster["name"]
    return None
