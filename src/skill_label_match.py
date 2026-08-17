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

3. **2026-08-17 real production incident**: skills_match()/
   previously_answered_skills-style dedup (drafting.py's
   _merge_keyword_gap_questions, request_additional_gap_questions, and
   discuss_and_draft.py's _generate_questions_via_subscription) only ever
   compares a proposed clarifying-question's "skill" LABEL against the
   "skill" LABEL of prior gap_interview_answers entries. It never looks at
   (a) the free-text ANSWER of those entries, or (b) the candidate's
   actual work_history/client_engagements bullets. Zahir was asked "The
   posting requires 'onshore/offshore teams' - do you have real, genuine
   experience with it?" even though that exact fact was both already
   confirmed - buried inside the free-text answer to a DIFFERENTLY-
   labeled gap question ("si partner relationships", answered 2026-08-07)
   - and stated directly across four real work_history/client_engagements
   bullets (Streebo, Univision, Eisai, The Brick). Label-vs-label matching
   can never catch either case, since neither one is stored under a
   matching "skill" label. build_already_known_units() and
   skill_evidenced_in_text() below are the deterministic backstop for
   this: a real word-level presence check against the candidate's actual
   profile TEXT (not just prior skill labels), so a fact genuinely already
   on record doesn't depend on the model itself noticing it's redundant -
   the same "AI output needs a code-level backstop, not just prompt
   wording" principle CLAUDE.md already documents for other cases.

   Deliberately UNIT-based, not one flattened blob - drafting.py's own
   _profile_narrative_units() (2026-08-10) was built specifically to avoid
   the false-positive shape of concatenating unrelated bullets into one
   string first (two bullets "Owned system design." and "Applications for
   internal tooling." would wrongly read as the compound phrase "design
   applications" if joined - see _profile_supports_skill's own docstring
   and test_profile_supports_skill_does_not_false_positive_across_
   unrelated_bullets). A first version of this fix built a single joined
   corpus string and reintroduced exactly that bug (caught before it
   shipped, by re-running that exact pre-existing test) - every check here
   is scoped to one real, independent unit (one gap-answer's full text, or
   one bullet) at a time, never text concatenated across units.
"""

import json
import re

_APOSTROPHE_RE = re.compile(r"['’]")
_OTHER_PUNCT_RE = re.compile(r"[^\w\s]")

# Deliberately small and conservative - only words common enough across
# almost any skill/question label to add no real discriminating signal
# ("do you have real, genuine experience with X" boilerplate words, plus
# basic function words). Keeping this short means a token only drops out
# when it's genuinely uninformative, not because it might occasionally be
# unimportant - erring toward keeping a word (stricter conjunctive match,
# fewer false positives) rather than dropping it (looser match, more risk
# of wrongly suppressing a genuinely distinct question).
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "real", "genuine", "experience", "it", "do", "you", "have",
}


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


def _significant_tokens(label: str) -> list[str]:
    normalized = normalize_skill_label(label)
    return [tok for tok in normalized.split() if tok not in _STOPWORDS and len(tok) > 1]


def _stem(token: str) -> str:
    """Bare-minimum plural stemming ("teams" -> "team"), just enough for
    skill_evidenced_in_text()'s prefix match to catch a label's plural
    form against a corpus bullet's singular form or vice versa (real case:
    a proposed question said "onshore/offshore teams" while the matching
    resume bullet said "team of 8 (onshore/offshore)" - singular). Not a
    real stemmer (no attempt at "ies"->"y", irregular plurals, etc.) -
    deliberately the smallest rule that closes the one real gap found,
    not a general NLP component."""
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def build_already_known_units(profile: dict | None) -> list[str]:
    """Real, INDEPENDENT textual units where a fact can already be
    genuinely known in the master profile - not just gap_interview_
    answers' short "skill" LABELS (already covered by skills_match()/
    previously_answered_skills-style checks), but the FULL free-text
    "answer" of every gap entry (a fact can be confirmed while buried
    inside the answer to a differently-labeled question - see this
    module's own docstring, item 3) and the candidate's actual
    work_history/client_engagements bullets (a fact stated directly on
    the resume was never "asked about" as a gap at all, so gap_interview_
    answers alone would never contain it either).

    Each gap-answer's full text, and each individual bullet/title, is its
    own separate unit - never concatenated together first (see this
    module's own docstring for the real cross-bullet false-positive shape
    that would otherwise reintroduce). skill_evidenced_in_text() below
    checks each unit independently and only needs ONE unit to satisfy
    every significant word of a label - it's still fine for facts spread
    across a single genuinely-long unit (e.g. one gap-answer paragraph
    that itself mentions both "onshore" and "offshore ... teams"), just
    never across two unrelated units."""
    if not profile:
        return []
    units: list[str] = []
    for entry in profile.get("gap_interview_answers", []) or []:
        answer = entry.get("answer")
        if answer:
            units.append(str(answer))
    for job in list(profile.get("work_history", []) or []) + list(profile.get("client_engagements", []) or []):
        title = job.get("title") or job.get("role") or ""
        if title:
            units.append(str(title))
        bullets = job.get("bullets") or []
        units.extend(str(b) for b in bullets if b)
        if not title and not bullets:
            # Real entries always have at least a title/role or bullets in
            # this app's own schema - this is just a defensive fallback so
            # an unexpectedly-shaped job dict (e.g. a test fixture) still
            # contributes SOMETHING rather than silently vanishing.
            units.append(json.dumps(job, default=str))
    return units


def skill_evidenced_in_text(skill_label: str, units) -> bool:
    """True if every significant word of skill_label appears as a real
    whole-word-START within some SINGLE unit of `units` (case/punctuation-
    insensitive) - a deterministic check for whether a proposed
    clarifying-question's underlying fact is ALREADY stated in the
    candidate's own profile text, independent of whether its "skill"
    label happens to match a prior question's label (skills_match() only
    catches that narrower case).

    `units` is normally build_already_known_units()'s own list (a single
    string is also accepted, treated as one unit, for a caller with just
    one block of text to check). Checking is scoped to ONE unit at a
    time, deliberately never across units concatenated together - see
    this module's own docstring for the real cross-bullet false-positive
    this avoids (Mirror's 2026-08-10 fix, and this fix's own first,
    corrected draft).

    Matches at the start of a word but not necessarily the whole word
    (real example: "team" must match "teams"/"teamwork" within the SAME
    unit, not just an exact "team" - a bullet reading "team of 8
    (onshore/offshore)" is real evidence for a proposed "onshore/offshore
    teams" question even though that bullet itself never uses the exact
    word "teams"). Still requires a real word-boundary at the START of
    the match, so this stays a prefix match, never a bare mid-word
    substring (the same "it" in "credit" false-positive class
    skills_match()'s own docstring already guards against).

    Deliberately conjunctive within a unit (every significant word must
    appear in that SAME unit, not just one) with a floor of 2 significant
    tokens - a single-word label ("Databricks") would otherwise true-
    positive against unrelated prose far too easily; a genuine one-word-
    label repeat is exactly what skills_match()/previously_answered_
    skills already covers, so this function declines to touch that
    narrower, already-solved case and only adds coverage for genuinely
    multi-word, specific labels where a conjunctive within-unit match is
    a reliable signal (e.g. "onshore offshore teams" - see this module's
    docstring for the real case)."""
    tokens = _significant_tokens(skill_label)
    if len(tokens) < 2:
        return False
    if isinstance(units, str):
        units = [units]
    stemmed = [_stem(tok) for tok in tokens]
    for unit in units or []:
        normalized_unit = normalize_skill_label(unit)
        if not normalized_unit:
            continue
        if all(re.search(r"\b" + re.escape(tok), normalized_unit) for tok in stemmed):
            return True
    return False


def filter_questions_evidenced_in_profile(questions: list[dict], units) -> list[dict]:
    """Drops any proposed clarifying question whose "skill" is already
    evidenced in `units` (skill_evidenced_in_text()) - the deterministic
    pre-filter callers should run over a raw AI-proposed clarifying_questions
    list before it's shown to the user or merged into the persisted list,
    so a fact already on record (in a gap-answer's free text or an actual
    resume bullet) never reaches the user as a "do you have experience with
    this?" question, independent of whether the model itself noticed."""
    if not units:
        return list(questions)
    return [
        q for q in questions
        if not (q.get("skill") and skill_evidenced_in_text(q["skill"], units))
    ]


# 2026-08-17 target-driven QA loop (feature/target-driven-qa-loop): a
# real quality bar on top of the 3-round hard cap above - "if the
# questions are correct and not duplicated or the same question being
# asked in different ways then 2 rounds should be enough" (Zahir). The
# dedup fixed by filter_questions_evidenced_in_profile() above only
# catches a fact already stated in the candidate's own PROFILE text; it
# says nothing about a LATER round's question restating an EARLIER
# round's question for the SAME job, just reworded. A prompt instruction
# alone ("do not repeat, even reworded") already proved unreliable once
# today for a closely related problem (see this module's own docstring,
# item 3) - same class of gap, so this is a real deterministic backstop,
# not just better prompt wording.
_CROSS_ROUND_SIMILARITY_THRESHOLD = 0.6


def question_text_similarity(a: str, b: str) -> float:
    """Jaccard overlap of normalized, stopword-stripped, plural-stemmed
    significant tokens between two full question texts - a bounded,
    deterministic measure of "these two questions are asking about the
    same underlying gap, just worded differently". Not real semantic
    understanding (paraphrases sharing few literal words can still slip
    through) - deliberately the same class of small, mechanical backstop
    as skill_evidenced_in_text() above, not an NLP component."""
    tokens_a = {_stem(tok) for tok in _significant_tokens(a)}
    tokens_b = {_stem(tok) for tok in _significant_tokens(b)}
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


def filter_questions_not_asked_before(questions: list[dict], prior_question_texts: list[str]) -> list[dict]:
    """Drops any proposed question whose full "question" text is a
    near-duplicate (question_text_similarity() >= threshold) of a
    question already asked in an EARLIER round of the same job's
    target-driven QA loop. Compares full question WORDING, not just the
    "skill" label - the same skill label is expected to legitimately
    recur across rounds until it's actually answered, so it's the
    QUESTION'S WORDING that must not repeat once a round has already put
    it in front of the user, not the skill it's about.

    Callers pass the accumulated real question text from every prior
    round of THIS job (subscription_resume_qa.py's asked-question
    history) - never another job's history, and never reset mid-loop."""
    if not prior_question_texts:
        return list(questions)
    kept = []
    for q in questions:
        text = q.get("question") or ""
        if text and any(
            question_text_similarity(text, prior) >= _CROSS_ROUND_SIMILARITY_THRESHOLD
            for prior in prior_question_texts
        ):
            continue
        kept.append(q)
    return kept
