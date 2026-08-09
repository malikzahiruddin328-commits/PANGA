"""Shared matching for AI-generated skill/question labels (Mirror's
proactive sweep, 2026-08-08 - two real, currently-latent findings):

1. profile/interview.py's save_answer() dedups a re-answered question
   against a prior entry via exact-string match (entry["skill"] == skill)
   - an AI-generated, non-enum-constrained label. If the model phrases the
   same underlying fact differently across two rounds ("AWS" vs "cloud
   infrastructure"), the exact match silently fails, creating a duplicate
   entry instead of updating the existing one - defeating the very fix
   built for that 2026-08-06.
2. drafting.py's _merge_keyword_gap_questions() deduped via bidirectional
   BARE substring containment ("x in y or y in x") - the same class of bug
   as this week's proven "it" pronoun/BSc case-sensitivity fixes in
   ats_score.py: a short label like "IT" is a bare substring of plenty of
   unrelated words ("credit", "legitimate"), so it could wrongly suppress
   a genuinely distinct question, while a differently-worded-but-
   equivalent label wouldn't substring-match at all and shows up as a
   spurious duplicate to the user.

Mirror's own confirmed-safe precedent nearby (drafting.py's
is_disqualifier check) is schema/enum-constrained, not string-matched -
that's not available here (skill labels are genuinely free text, chosen
fresh by the model each round, not drawn from a fixed set), so this
normalizes + requires a real word-boundary-respecting match instead of a
bare substring - a bounded, deterministic improvement over both exact-
match fragility and substring false-positives. It does NOT solve genuine
semantic drift ("AWS" vs "cloud infrastructure" are still different
strings after normalization) - that would need real language
understanding, out of scope for a small, currently-latent gap; this fixes
the mechanical string-matching failure modes Mirror actually found.
"""

import re

_APOSTROPHE_RE = re.compile(r"['’]")
_OTHER_PUNCT_RE = re.compile(r"[^\w\s]")


def normalize_skill_label(label: str) -> str:
    """Case/punctuation/whitespace-insensitive normalization of a skill
    label - the shared building block behind skills_match() below and
    also used directly by drafting.py's generic-soft-skill deny-list
    (2026-08-09), which needs plain normalized-equality against a curated
    set rather than skills_match()'s looser phrase-containment (a
    deny-list has to be exact-ish, not fuzzy - phrase-containment would
    risk dropping a real keyword that happens to contain a denied phrase
    as a whole word, e.g. "Presentation Layer Architecture" containing
    "presentation"). Public (not module-private) for that reuse - was
    private until a second real caller outside this module needed it."""
    # Apostrophes are removed outright ("Master's" -> "Masters"), not
    # replaced with a space like other punctuation - otherwise "Master's
    # Degree" and "masters degree" normalize to different token sequences
    # ("master s degree" vs "masters degree") and wrongly fail to match.
    without_apostrophes = _APOSTROPHE_RE.sub("", label.lower())
    normalized = _OTHER_PUNCT_RE.sub(" ", without_apostrophes)
    return " ".join(normalized.split())


def skills_match(a: str, b: str) -> bool:
    """True if two skill/question labels plausibly refer to the same
    underlying fact: normalized (case/punctuation/whitespace-insensitive)
    equality, or one being a real, word-boundary-respecting phrase within
    the other - never a bare substring."""
    norm_a, norm_b = normalize_skill_label(a), normalize_skill_label(b)
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True
    pattern_a = r"\b" + re.escape(norm_a) + r"\b"
    pattern_b = r"\b" + re.escape(norm_b) + r"\b"
    return bool(re.search(pattern_a, norm_b) or re.search(pattern_b, norm_a))
