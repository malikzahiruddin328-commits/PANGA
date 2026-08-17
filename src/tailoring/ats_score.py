"""Deterministic ATS compatibility scoring.

Replaces the old approach (drafting.py's resume schema asking the same API
call that WROTE the resume to also self-grade it via a free-text
"ats_score" field): that was an independent, memoryless AI guess every
single call, with no actual comparison happening, which is why the number
never moved no matter what Zahir changed. This module does real text
comparison instead - given a required/preferred keyword list, it counts
literal overlap against the drafted resume text, plus a few structural/
formatting checks. The result is a score that visibly and correctly moves
when a keyword is added or a gap is filled, because it is arithmetic on the
actual text, not a guess.

Two ways to get the keyword list, both feeding the same scoring math:
- score_resume_against_keywords(required, preferred, resume_text): the
  primary path - drafting.py calls this with a keyword list a single AI
  call already extracted from the job posting (real NLP judgment, the
  thing AI is actually good at) and cached on the job record, so the same
  posting always scores against the same keyword list rather than a fresh
  guess every regenerate.
- score_resume_ats(posting_text, resume_text): a dependency-free heuristic
  fallback (no NLP library is installed in this project - see
  requirements.txt) for when AI extraction hasn't run or isn't available
  (no API key configured, or a transient failure) - regex-based, will not
  perfectly segment every posting's prose, but still real, reproducible
  math over the actual text rather than an AI self-grade.
"""

import re

from skill_label_match import normalize_skill_label

# Degree-TIER phrases eligible for the years-of-experience equivalency
# credit below - deliberately excludes baseline "Bachelor's degree" (Zahir's
# explicit scoping, 2026-08-09: the equivalency is specifically for
# advanced-degree-tier requirements, not the baseline credential).
_ADVANCED_DEGREE_TIER_PHRASES = {
    normalize_skill_label(p) for p in (
        "advanced degree", "master's degree", "graduate degree",
        "doctoral degree", "doctorate", "phd",
    )
}
_ADVANCED_DEGREE_EQUIVALENCY_YEARS = 10
_YEARS_EQUIVALENCY_MARKER = "years-of-experience equivalency"


def _is_advanced_degree_tier_keyword(label: str) -> bool:
    return normalize_skill_label(label) in _ADVANCED_DEGREE_TIER_PHRASES


# Generic filler/boilerplate words that show up in JD prose but are never
# themselves a skill/keyword worth matching on - kept broad on purpose so
# capitalized-word extraction (which catches real proper nouns like
# "Python") doesn't also pick up ordinary sentence-initial words.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "in", "on", "for",
    "to", "with", "as", "by", "at", "from", "is", "are", "be", "will",
    "this", "that", "these", "those", "you", "your", "our", "we", "who",
    "their", "have", "has", "had", "not", "can", "may", "must", "should",
    "into", "than", "then", "such", "also", "any", "all", "other", "more",
    "most", "some", "including", "etc", "per", "across", "within", "about",
    "up", "out", "over", "under", "each", "both", "either", "neither",
    "job", "role", "position", "candidate", "candidates", "company",
    "team", "work", "working", "years", "year", "experience", "ability",
    "abilities", "skills", "skill", "strong", "excellent", "demonstrated",
    "proven", "knowledge", "understanding", "familiarity", "proficiency",
    "responsible", "responsibilities", "interested", "hiring", "decisions",
    "decision", "based", "committed", "improving", "curiosity",
    "creativity", "initiative", "meet", "minimum", "desired", "required",
    "requirements", "requirement", "qualifications", "qualification",
    "prior", "background", "investigation", "citizenship", "national",
    "origin", "religion", "color", "race", "sex", "rule", "law",
    "government", "federal", "environment", "independently", "groups",
    "opportunity", "organization", "organizations", "join", "joining",
    "what", "we're", "you'll", "you're", "need", "bring", "looking",
    "ideal", "make", "impact", "day", "one", "own", "lead", "leading",
    "drive", "driving", "build", "building", "partner", "partners",
    "across", "high", "growth", "enterprise", "senior", "leadership",
    "american", "republic", "ideals", "passionate", "constitution",
    "upholding", "including", "u.s", "u.s.", "national",
}

# Filler lead-ins stripped off the front of a comma/semicolon-split JD
# clause before it's judged as a keyword candidate - e.g. "5+ years of
# experience with Python" -> "Python".
_FILLER_PREFIX_RE = re.compile(
    r"^(?:"
    r"\d+\+?\s*years?\s*(?:of\s*)?(?:experience\s*)?(?:with|in)?\s*"
    r"|experience\s+(?:with|in)\s+"
    r"|knowledge\s+of\s+"
    r"|proficiency\s+(?:with|in)\s+"
    r"|familiarity\s+with\s+"
    r"|working\s+knowledge\s+of\s+"
    r"|ability\s+to\s+"
    r"|understanding\s+of\s+"
    r"|strong\s+|excellent\s+|demonstrated\s+|proven\s+"
    r")+",
    re.IGNORECASE,
)

_REQUIRED_MARKER_RE = re.compile(
    r"(?:minimum|required|require[sd]?|basic|essential)\s+qualifications?"
    r"|requirements?\s*:"
    r"|must[- ]have"
    r"|what you.?ll need"
    r"|required skills?",
    re.IGNORECASE,
)
_PREFERRED_MARKER_RE = re.compile(
    r"(?:desired|preferred)\s+qualifications?"
    r"|preferred\s*:"
    r"|nice[- ]to[- ]have"
    r"|bonus(?:\s+points)?"
    r"|pluses?\b",
    re.IGNORECASE,
)

_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")
_TECH_SYMBOL_RE = re.compile(
    r"\b[A-Za-z]+(?:[+#]){1,2}\b"          # C++, C#
    r"|\b[A-Za-z]{2,}/[A-Za-z]{2,}\b"      # AI/ML, CI/CD
)
_CAPWORD_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")

_MAX_KEYWORDS_PER_TIER = 25
_MAX_PHRASE_WORDS = 4


def _clean_phrase(phrase: str) -> str:
    phrase = phrase.strip(" \t.;:-•")
    phrase = _FILLER_PREFIX_RE.sub("", phrase).strip(" \t.;:-")
    return phrase


def _is_meaningful(phrase: str) -> bool:
    words = phrase.split()
    if not words or len(words) > _MAX_PHRASE_WORDS:
        return False
    if all(w.lower().strip(".,") in _STOPWORDS for w in words):
        return False
    if len(phrase) < 2:
        return False
    return True


def _candidate_phrases(segment: str) -> list[str]:
    found = []
    for chunk in re.split(r",|;|:|\.(?!\w)|(?<!\w)/(?!\w)| or | and ", segment):
        phrase = _clean_phrase(chunk)
        if phrase and _is_meaningful(phrase):
            found.append(phrase)
    for regex in (_TECH_SYMBOL_RE, _ACRONYM_RE, _CAPWORD_RE):
        for m in regex.finditer(segment):
            token = m.group(0)
            if token.lower() not in _STOPWORDS and len(token) >= 2:
                found.append(token)
    return found


def _segment_by_markers(text: str) -> list[tuple[bool | None, str]]:
    """Splits `text` into (is_required, segment) chunks using required/
    preferred marker phrases found anywhere in the text (not just on their
    own line - real scraped JD text is often one continuous paragraph with
    no line breaks at all, so a line-anchored header check would miss
    almost everything). Text before the first marker is tagged None
    (unclassified boilerplate, e.g. "About Us") and excluded from
    extraction unless no marker is found anywhere, in which case the whole
    text becomes a single required-weighted segment (fallback so postings
    with unusual formatting still yield some real signal)."""
    markers = []
    for m in _REQUIRED_MARKER_RE.finditer(text):
        markers.append((m.start(), m.end(), True))
    for m in _PREFERRED_MARKER_RE.finditer(text):
        markers.append((m.start(), m.end(), False))
    if not markers:
        return [(True, text)]

    markers.sort(key=lambda t: t[0])
    segments = []
    for i, (start, end, is_required) in enumerate(markers):
        seg_end = markers[i + 1][0] if i + 1 < len(markers) else len(text)
        segments.append((is_required, text[end:seg_end]))
    return segments


def extract_keywords(posting_text: str) -> dict[str, bool]:
    """Returns {lowercased_keyword_phrase: is_required} extracted from the
    job posting's own text - no external lexicon, no AI guess. A phrase
    found in both a required and a preferred segment counts as required."""
    if not posting_text or not posting_text.strip():
        return {}

    keywords: dict[str, bool] = {}
    for is_required, segment in _segment_by_markers(posting_text):
        if is_required is None:
            continue
        for phrase in _candidate_phrases(segment):
            lowered = phrase.lower()
            keywords[lowered] = keywords.get(lowered, False) or is_required

    required = [k for k, v in keywords.items() if v][:_MAX_KEYWORDS_PER_TIER]
    preferred = [k for k, v in keywords.items() if not v][:_MAX_KEYWORDS_PER_TIER]
    return {**{k: True for k in required}, **{k: False for k in preferred}}


# Real gap Zahir hit live 2026-08-07: pure literal matching had no idea
# "BSc"/"Bachelor of Science" satisfies a posting's "bachelor's degree",
# or that "IT" satisfies "information technology" - so
# score_resume_against_keywords() marked these missing_required_keywords
# even when plainly present, and that false-missing list flows straight
# into drafting.py's _merge_keyword_gap_questions() as a real, user-facing
# "do you have this?" question about something already on the resume.
# Deterministic lookup table, not an AI guess or NLP library (this module
# is dependency-free by design, see the module docstring) - same
# philosophy as tailoring.drafting's rank-prefix/seniority safety nets:
# a bounded, explicit equivalence map beats leaving a known failure mode
# to chance. Each inner set is one equivalence class - membership is
# symmetric (a required "bsc" matches a resume's "bachelor's degree" just
# as well as the reverse), so this fixes both score_resume_against_keywords()
# (AI-extracted keyword list) and score_resume_ats()'s no-AI fallback
# (extract_keywords() below) identically, since both funnel through the
# same _phrase_in_text() call.
_KEYWORD_EQUIVALENCE_GROUPS = [
    {
        "bachelor's degree", "bachelors degree", "bachelor degree", "undergraduate degree",
        "bachelor of science", "bachelor of arts", "bsc", "b.sc", "b.sc.",
        "ba", "b.a", "b.a.", "bs", "b.s", "b.s.",
    },
    {
        "master's degree", "masters degree", "master degree", "graduate degree",
        "master of science", "master of arts", "master of business administration",
        "msc", "m.sc", "m.sc.",
        "mba", "m.b.a", "m.b.a.",
        # Deliberately NOT "ma"/"m.a"/"ms"/"m.s" (2026-08-10, real gap found
        # live: Zahir's own profile has "Burlington, MA"/"Waltham, MA" as
        # real US-state location text, and "MS Copilot"/"MS Office" as real
        # tool mentions - both realistically appear CAPITALIZED in ordinary
        # resume prose, unlike "it", so the same case-sensitive-acronym
        # backstop that fixed the "IT" bug (2026-08-07) doesn't protect
        # here; a real "Burlington, MA" or "MS Copilot" line silently
        # satisfied an unmet "Master's degree"/"MBA" requirement with no
        # gap question ever surfacing. "msc"/"m.sc" are kept - three-letter/
        # punctuated forms don't collide with a common two-letter US-state
        # or product-name abbreviation the same way. A candidate whose
        # resume states only a bare "MA"/"MS" (never "Master of Arts/
        # Science" or "Master's degree") no longer gets automatic credit -
        # accepted tradeoff, the same direction CLAUDE.md's own doctrine
        # prefers (a missed match a human can correct beats a silent false
        # match nobody ever sees).
    },
    {"doctorate", "doctoral degree", "phd", "ph.d", "ph.d."},
    {"information technology", "it"},
    {"computer science", "cs"},
]
_KEYWORD_ALIASES: dict[str, frozenset[str]] = {}
for _group in _KEYWORD_EQUIVALENCE_GROUPS:
    _frozen_group = frozenset(_group)
    for _term in _group:
        _KEYWORD_ALIASES[_term] = _frozen_group


# Short acronym-style aliases that are ALSO ordinary English words/tokens
# when lowercased - "it" being the standout risk (one of the most common
# words in English). Real gap found 2026-08-07 (General's review, before
# this shipped): matching these case-insensitively against the fully-
# lowercased resume text meant any resume containing an ordinary sentence
# like "delivered it on time" would silently satisfy "information
# technology" - the opposite failure mode from the bug this equivalence
# table exists to fix, and worse, because it's silent (no gap question
# ever gets asked, so a real gap stays hidden instead of being surfaced).
# These are checked case-sensitively against the ORIGINAL (non-lowercased)
# text instead - only count if the token actually appears capitalized/
# uppercase there, the way a real acronym would. Multi-word aliases
# ("information technology", "bachelor's degree") stay case-insensitive -
# they're unambiguous, no common English phrase collides with them.
_CASE_SENSITIVE_ALIASES = {"it", "cs", "bsc", "ba", "bs", "mba", "phd"}


def _literal_phrase_in_text(phrase: str, text_lower: str, text_original: str) -> bool:
    # Periods stripped from both sides before matching so "b.sc"/"ph.d."-
    # style aliases line up with however the actual text punctuates them
    # ("BSc", "B.S.C", "PhD", "Ph.D." all normalize the same way).
    phrase = phrase.replace(".", "")
    if phrase in _CASE_SENSITIVE_ALIASES:
        original_depunct = text_original.replace(".", "")
        pattern = r"\b" + re.escape(phrase) + r"\b"
        return any(
            m.group(0) != m.group(0).lower()
            for m in re.finditer(pattern, original_depunct, re.IGNORECASE)
        )
    text_lower = text_lower.replace(".", "")
    if re.match(r"^[\w\s']+$", phrase):
        pattern = r"\b" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"\b"
        return re.search(pattern, text_lower) is not None
    return phrase in text_lower


def _phrase_in_text(phrase: str, text_lower: str, text_original: str) -> bool:
    for alias in _KEYWORD_ALIASES.get(phrase, (phrase,)):
        if _literal_phrase_in_text(alias, text_lower, text_original):
            return True
    return False


# Pre-flight score verification (feature/basket-badge-verification,
# 2026-08-17) - Zahir's "hope and pray" complaint: the basket Q&A's "+N pts"
# badge on a clarifying question comes straight from missing_required_
# keywords/missing_preferred_keywords' point_value below, but nothing ever
# guaranteed that CONFIRMING the question's suggested_answer text would
# actually make that literal keyword phrase present in the eventual
# redrafted resume - an AI redraft is free to paraphrase instead of using
# the literal phrase, silently missing this same deterministic matcher
# later with zero visible warning. Since the matcher itself
# (score_resume_against_keywords, via _phrase_in_text above) is pure
# deterministic text comparison - not an AI call - a question's confirmed
# answer text can be test-checked (and, if it fails, patched) against the
# EXACT SAME matcher before the badge is ever shown, so the promise is
# actually true by construction instead of hoped for after the fact.
def keyword_literally_present(term: str, candidate_text: str) -> bool:
    """Does `candidate_text` already literally contain the keyword phrase
    `term` - a missing_required_keywords/missing_preferred_keywords "label"
    (see score_resume_against_keywords below), including an any_of group's
    " OR "-joined label (see _normalize_keyword_item) - using the EXACT SAME
    phrase-matching logic (equivalence aliases, the "IT"/"CS"/degree-
    abbreviation case-sensitive-acronym backstop, word-boundary matching)
    score_resume_against_keywords() itself uses via _phrase_in_text(), so
    this check and the real deterministic ATS score computed from the
    eventual resume text can never silently disagree about whether a fact
    is present. Reuses _phrase_in_text() rather than reimplementing
    matching - the two paths literally cannot drift apart, they're the same
    function.

    An any_of group's label is satisfied if candidate_text contains ANY ONE
    member (same "any alternative matches" semantics _match_keyword_item
    uses for a real group item - this just recovers the member list by
    splitting the joined label, since callers here only ever have the
    label string, not the original item dict, by the time a question's
    point_value has been computed)."""
    if not term or not candidate_text:
        return False
    text_lower = candidate_text.lower()
    members = term.split(" OR ") if " OR " in term else [term]
    return any(_phrase_in_text(m.strip().lower(), text_lower, candidate_text) for m in members)


def ensure_keyword_literally_present(term: str, candidate_text: str) -> tuple[str, bool]:
    """Guarantees `term` is literally present in the returned text BY
    CONSTRUCTION, rather than leaving it to chance that a later AI redraft
    happens to phrase it that way (see keyword_literally_present's docstring
    for the "hope and pray" problem this closes). Returns
    (text_to_use, was_already_present).

    If `candidate_text` already contains the literal phrase, returns it
    completely unchanged (True) - never rewrites text that's already
    honest and correct. This also makes the function idempotent /
    duplicate-insertion-safe (2026-08-17, same principle skill_label_match.
    py's own dedup backstops use: check presence before acting, never
    assume state) - calling this twice on the same text, e.g. once when a
    question is first generated and again after the user edits their
    answer, never appends the clause a second time once the term is
    already there, whether that's because this function put it there or
    the user's own edit already covers it.

    Otherwise, deterministically weaves the literal term in:
    - If there's no existing text (a genuinely blank/no-basis starting
      point), the result is an honest hedge asking about the term by name
      ("Please describe your real experience (if any) with ...") - NEVER
      an assertion that the candidate has it. Fabricating a claim from
      nothing would be worse than the "hope and pray" bug this exists to
      fix (Zahir's explicit instruction: rephrase/append using terms
      consistent with what's actually being confirmed, never invent a new
      claim).
    - If there's existing substantive text (a hedged AI guess, or a
      deterministic starting guess), the term is appended as a short,
      natural-reading parenthetical naming it explicitly - the same "short
      clause append, not obvious keyword-stuffing" shape as the design
      spec's own example ("... via SAP and CMO/3PL relationships
      (shop-floor systems).").

    Re-verifies the patched text against keyword_literally_present() before
    returning - this function's whole reason to exist is to make the
    guarantee actually true, not just plausible, so it never returns text
    it hasn't itself confirmed satisfies the check."""
    if keyword_literally_present(term, candidate_text):
        return candidate_text, True

    clause_term = term if " OR " not in term else " or ".join(
        m.strip() for m in term.split(" OR ")
    )
    base = (candidate_text or "").strip()
    if not base:
        patched = f'Please describe your real experience (if any) with "{clause_term}".'
    else:
        if not base.endswith((".", "!", "?")):
            base += "."
        patched = f'{base} (specifically: "{clause_term}")'

    if not keyword_literally_present(term, patched):
        # Defensive fallback - shouldn't be reachable (the clause above
        # literally contains clause_term's own text), but this function
        # never claims a guarantee it hasn't actually verified.
        patched = f'{patched} "{clause_term}"'.strip()

    return patched, False


# Score-first-resume-flow spec (docs/score-first-resume-flow-spec.md, item
# 2): a JD routinely phrases a requirement as a substitutable either/or -
# "Master's degree, OR Bachelor's degree plus 8+ years of experience." The
# old flat-list matcher had no way to represent this: it extracted
# "Master's degree" as its own independent required keyword, so a
# candidate who genuinely satisfies the Bachelor's+experience side got
# dinged for a "gap" that was never real. A keyword item is now either a
# plain string (unchanged behavior) or {"any_of": [alt1, alt2, ...]} - a
# single satisfiable group, matched if ANY alternative matches.
def _normalize_keyword_item(item) -> dict:
    if isinstance(item, dict) and "any_of" in item:
        raw_members = item.get("any_of")
        if not isinstance(raw_members, list):
            # Malformed shape (2026-08-10, real gap found): a non-list
            # any_of value used to be iterated character-by-character
            # (Python iterates a bare string one letter at a time), turning
            # e.g. {"any_of": "Bachelor's degree"} into a 17-member group of
            # single letters that trivially matched almost any resume text -
            # a required item silently reading as satisfied when it may not
            # have been at all. The live schema-validated extraction call
            # can't produce this (any_of is typed as a JSON array there) -
            # this only guards a stale cached job record or a manual data
            # edit that predates that enforcement. Treated as invalid,
            # excluded from scoring entirely (see the is_invalid filter in
            # score_resume_against_keywords) rather than guessing whether
            # "satisfied" or "missing" is the safer direction to fabricate.
            return {"label": None, "members": [], "is_group": True, "is_invalid": True}
        # Real gap found the same pass: item.get("any_of") used to be
        # checked for TRUTHINESS, not presence - a schema-valid (no
        # minItems declared) but empty {"any_of": []} is falsy, so this
        # silently fell through to the plain-string branch below and
        # rendered as the literal Python repr "{'any_of': []}" in
        # missing_required_keywords, visible to Zahir as if it were a real
        # skill to confirm. An any_of group with zero named alternatives
        # has nothing to fail against, so it's vacuously satisfied instead
        # (see the empty-members short-circuit in _match_keyword_item).
        members = [str(m) for m in raw_members]
        label = " OR ".join(members) if members else "(any_of group with no alternatives - vacuously satisfied)"
        return {"label": label, "members": members, "is_group": True, "is_invalid": False}
    return {"label": str(item), "members": [str(item)], "is_group": False, "is_invalid": False}


_VACUOUS_GROUP_MATCH = "(no alternatives specified)"


def _match_keyword_item(item: dict, text_lower: str, text_original: str) -> str | None:
    """Returns the specific member that satisfied this item (itself, for a
    plain string), or None if nothing in it matched. An any_of group with
    zero real members (see _normalize_keyword_item) has nothing to fail
    against - vacuously satisfied rather than treated as an unmet
    requirement."""
    if item["is_group"] and not item["members"]:
        return _VACUOUS_GROUP_MATCH
    for member in item["members"]:
        if _phrase_in_text(member.lower(), text_lower, text_original):
            return member
    return None


# Public (not module-private) - reused by tailoring.claim_verification
# (2026-08-09) to reliably detect a section BOUNDARY (not just "any all-
# caps line," which would misfire on an all-caps acronym-style
# certification like "PMP") - same "make it public once a second real
# caller needs it" precedent as skill_label_match.normalize_skill_label.
STANDARD_HEADERS = (
    "professional experience", "experience", "work experience",
    "education", "skills", "core skills", "technical skills",
    "certifications", "summary", "professional summary",
)
_MARKDOWN_ARTIFACT_RE = re.compile(r"\*\*|^\s*#|\| *[-:]+ *\||^\s*\* ", re.MULTILINE)
_DATE_RE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(\+?\d[\d\-.() ]{8,}\d)")


def _structure_score(resume_text: str) -> tuple[float, list[str]]:
    """0-100 formatting/parseability score plus a list of failed-check
    labels (used to generate concrete next_actions)."""
    text_lower = resume_text.lower()
    failed = []

    has_headers = sum(1 for h in STANDARD_HEADERS if h in text_lower) >= 2
    if not has_headers:
        failed.append("headers")

    has_markdown = bool(_MARKDOWN_ARTIFACT_RE.search(resume_text))
    if has_markdown:
        failed.append("markdown")

    has_dates = bool(_DATE_RE.search(resume_text))
    if not has_dates:
        failed.append("dates")

    has_contact = bool(_EMAIL_RE.search(resume_text[:400]) or _PHONE_RE.search(resume_text[:400]))
    if not has_contact:
        failed.append("contact")

    checks = [has_headers, not has_markdown, has_dates, has_contact]
    return 100.0 * sum(checks) / len(checks), failed


def score_resume_ats(posting_text: str, resume_text: str) -> dict:
    """Dependency-free fallback: heuristically extracts keywords from
    `posting_text` via extract_keywords() (regex-based, no AI call), then
    scores against them. Use score_resume_against_keywords() instead
    whenever an AI-extracted keyword list is available - see module
    docstring."""
    keywords = extract_keywords(posting_text)
    required = [k for k, v in keywords.items() if v]
    preferred = [k for k, v in keywords.items() if not v]
    return score_resume_against_keywords(required, preferred, resume_text)


def _score_from_counts(
    matched_required: int, total_required: int, matched_preferred: int, total_preferred: int, structure_score: float,
) -> float:
    """The scoring formula itself, isolated from keyword matching - lets
    point-value computation below re-run the SAME formula with one more
    hypothetical match rather than hand-deriving an approximation that
    could silently drift from what this function actually does. Returns
    an unrounded float; callers round only the final displayed score."""
    required_coverage = (matched_required / total_required) if total_required else None
    preferred_coverage = (matched_preferred / total_preferred) if total_preferred else None

    if required_coverage is not None and preferred_coverage is not None:
        keyword_score = 100.0 * (0.75 * required_coverage + 0.25 * preferred_coverage)
    elif required_coverage is not None:
        keyword_score = 100.0 * required_coverage
    elif preferred_coverage is not None:
        keyword_score = 100.0 * preferred_coverage
    else:
        keyword_score = None

    ats_score = structure_score if keyword_score is None else 0.75 * keyword_score + 0.25 * structure_score
    return max(0.0, min(100.0, ats_score))


def plateau_note_for_gaps(missing_required: list[dict], matched_group_explanations: list[str]) -> str | None:
    """Score-first-resume-flow spec item 2's second half: the system, not
    the user, should notice when the score has genuinely plateaued and say
    why in plain terms - rather than leaving Zahir to wonder "why did this
    go up/down/nowhere." None when there's nothing notable to explain
    (no remaining required gaps and no either/or group worth calling out).

    Public (not module-private) so drafting.py's analyze_fit_before_drafting
    can recompute this against a NARROWED missing_required list - one with
    already-answered-but-not-yet-drafted gaps removed - rather than
    reimplementing this exact sentence-building logic a second time (real
    gap found live 2026-08-09: the raw score_resume_against_keywords()
    output has no notion of "answered in the profile already," so its own
    plateau_note kept naming a gap as "real, not a phrasing issue" even
    after the candidate had genuinely answered it - just not yet reflected
    in a fresh draft)."""
    if not missing_required and not matched_group_explanations:
        return None
    parts = []
    if matched_group_explanations:
        parts.append(
            "Some requirements are alternatives you already satisfy a different way: "
            + "; ".join(matched_group_explanations) + "."
        )
    if missing_required:
        labels = ", ".join(f'"{m["label"]}"' for m in missing_required)
        parts.append(
            f"The remaining gap is real, not a phrasing issue: {labels}. Answering more "
            "about your background will only raise this further if you genuinely have "
            "this specific experience."
        )
    return " ".join(parts)


def _dedupe_flat_items(items) -> list[dict]:
    """Collapses exact-duplicate FLAT keywords within one tier (2026-08-10,
    real gap found: an accidental duplicate extraction of the same plain
    keyword - not the documented legitimate case below - quietly dilutes
    point_value for every OTHER missing keyword, since each copy counts
    independently toward total_required/total_preferred; quantified on a
    synthetic 5-keyword set, an accidental duplicate inflated the score
    from 66 to 69 and understated a real gap's point_value from 15.0 to
    12.5). Deliberately does NOT touch any_of GROUPS, and does NOT dedupe a
    flat keyword against a same-named any_of GROUP MEMBER - the resume-
    drafting prompt has an explicit, legitimate rule for a term needing
    extraction in both roles at once (e.g. "Supply Chain Management" as
    both an acceptable degree field AND its own substantive-experience
    requirement) - those are two genuinely different requirements sharing
    a label, not a duplicate of the same one, and only two IDENTICAL flat
    strings can never represent two distinct requirements the way a
    flat-vs-group-member pair legitimately can."""
    seen = set()
    deduped = []
    for item in items:
        if not item["is_group"]:
            key = normalize_skill_label(item["label"])
            if key in seen:
                continue
            seen.add(key)
        deduped.append(item)
    return deduped


def score_resume_against_keywords(
    required_keywords: list, preferred_keywords: list, resume_text: str,
    candidate_years_experience: float | None = None,
) -> dict:
    """The real, deterministic ATS score for one drafted resume against an
    already-extracted required/preferred keyword list. Each item is either
    a plain string (case-insensitive, lowercase or not) or a
    {"any_of": [alt1, alt2, ...]} either/or group, satisfied if ANY
    alternative matches (score-first-resume-flow spec item 2 - a JD's
    "Master's degree, OR Bachelor's degree plus 8+ years" shouldn't score
    as a flat, independently-required "Master's degree").

    Returns {"ats_score": int 0-100, "ats_rationale": str,
    "ats_next_actions": [str, ...],
    "missing_required_keywords": [{"label": str, "point_value": float}, ...],
    "missing_preferred_keywords": [{"label": str, "point_value": float}, ...],
    "matched_group_explanations": [str, ...],
    "plateau_note": str | None}. Always recomputed from the actual text
    passed in - the score moves when the text does, because this is
    literal keyword-overlap arithmetic plus formatting checks, not an
    independent AI guess.

    missing_preferred_keywords/matched_group_explanations (2026-08-09)
    are the same real per-keyword numbers already computed to build
    ats_next_actions/plateau_note internally, just also handed back to the
    caller - drafting.py's analyze_fit_before_drafting needs them to
    attach a real point_value to free-form AI clarifying_questions that
    happen to correspond to a preferred (not required) keyword, and to
    recompute an honest plateau_note once already-answered-but-not-yet-
    drafted gaps are excluded - both reuse this module's own arithmetic
    rather than re-deriving it.

    missing_required_keywords is returned separately from ats_next_actions
    (2026-08-06, real gap Zahir hit live): these are the specific,
    answerable "does the candidate actually have this" gaps, and
    drafting.py merges them into the resume's real clarifying_questions
    flow (the same interactive, savable mechanism Profile Gaps already
    uses) rather than leaving them as inert bullet-point text with no way
    to answer or act on them - see
    tailoring.drafting._merge_keyword_gap_questions(). Each carries a
    point_value (2026-08-08, score-first-resume-flow spec item 2/UI
    contract) - computed by re-running _score_from_counts() with one more
    hypothetical match, not a separately hand-derived formula that could
    drift from the real one. ats_next_actions keeps only what's genuinely
    just informational/directive (structural formatting fixes, optional
    preferred-keyword suggestions) - things there's no real fact to
    "answer", just an edit to make.

    candidate_years_experience (2026-08-09, Zahir's explicit design,
    confirmed and scoped by General): a standing, candidate-level scoring
    policy, NOT a resume-text change - never writes anything into the
    resume. If a flat (non-any_of) required/preferred keyword is a
    degree-TIER phrase (see _ADVANCED_DEGREE_TIER_PHRASES - "advanced
    degree"/"Master's degree" etc., deliberately excluding baseline
    "Bachelor's degree") and isn't literally matched in the resume text,
    but candidate_years_experience is at least _ADVANCED_DEGREE_EQUIVALENCY_
    YEARS, it's credited as satisfied via this documented equivalency
    instead. Any_of GROUP members are never eligible for this credit -
    if the posting already states its own equivalency ("Master's OR
    Bachelor's plus 8+ years"), that's exactly what gets extracted as an
    any_of group in the first place, so the group's own literal-match
    logic already covers it; applying the credit there too would double
    up on an equivalency the posting already spelled out itself. Fully
    transparent in ats_rationale (never silently blended in as if it
    were a literal keyword match - this has to be auditable). Real
    motivating case: Upstream Bio's "advanced degree preferred" (6.2 pts)
    - Zahir has 25+ years of experience, no advanced degree, and the JD
    states no equivalency of its own."""
    # Malformed items (see _normalize_keyword_item - a non-list any_of,
    # reachable only via a stale cached job record, never the live
    # schema-validated extraction call) are excluded entirely rather than
    # scored as satisfied or missing - there's no safe way to guess which
    # direction a genuinely invalid entry "should" count.
    required_items = _dedupe_flat_items(i for i in (_normalize_keyword_item(k) for k in required_keywords) if not i["is_invalid"])
    preferred_items = _dedupe_flat_items(i for i in (_normalize_keyword_item(k) for k in preferred_keywords) if not i["is_invalid"])
    resume_lower = resume_text.lower()

    required_matches = [_match_keyword_item(item, resume_lower, resume_text) for item in required_items]
    preferred_matches = [_match_keyword_item(item, resume_lower, resume_text) for item in preferred_items]

    equivalency_explanations = []
    if candidate_years_experience is not None and candidate_years_experience >= _ADVANCED_DEGREE_EQUIVALENCY_YEARS:
        def _apply_years_equivalency(items, matches):
            for i, (item, match) in enumerate(zip(items, matches)):
                if match is None and not item["is_group"] and _is_advanced_degree_tier_keyword(item["label"]):
                    matches[i] = _YEARS_EQUIVALENCY_MARKER
                    equivalency_explanations.append(
                        f"\"{item['label']}\" credited via {candidate_years_experience:.0f}+ years of "
                        "relevant experience equivalency (not stated in the resume text itself, and the "
                        "posting states no equivalency of its own)"
                    )
        _apply_years_equivalency(required_items, required_matches)
        _apply_years_equivalency(preferred_items, preferred_matches)

    total_required = len(required_items)
    total_preferred = len(preferred_items)
    matched_required_count = sum(1 for m in required_matches if m)
    matched_preferred_count = sum(1 for m in preferred_matches if m)

    structure_score, failed_checks = _structure_score(resume_text)

    ats_score_float = _score_from_counts(
        matched_required_count, total_required, matched_preferred_count, total_preferred, structure_score,
    )
    ats_score = max(0, min(100, round(ats_score_float)))

    matched_group_explanations = [
        f"\"{item['label']}\" satisfied via: {match}"
        for item, match in zip(required_items + preferred_items, required_matches + preferred_matches)
        if item["is_group"] and match
    ]

    total_kw = total_required + total_preferred
    matched_kw = matched_required_count + matched_preferred_count
    if total_kw:
        rationale = (
            f"Matched {matched_kw}/{total_kw} keywords/skills extracted from the "
            f"posting ({matched_required_count}/{total_required} required, "
            f"{matched_preferred_count}/{total_preferred} preferred); "
            f"formatting/structure check {round(structure_score)}%."
        )
        if matched_group_explanations:
            rationale += " " + "; ".join(matched_group_explanations) + "."
        if equivalency_explanations:
            rationale += " " + "; ".join(equivalency_explanations) + "."
    else:
        rationale = (
            "This posting's stored text didn't have distinct extractable "
            f"requirements - score reflects resume structure/formatting only "
            f"({round(structure_score)}%)."
        )

    missing_required = [
        {
            "label": item["label"],
            "point_value": round(
                _score_from_counts(matched_required_count + 1, total_required, matched_preferred_count, total_preferred, structure_score)
                - ats_score_float,
                1,
            ),
        }
        for item, match in zip(required_items, required_matches) if not match
    ]
    missing_preferred = [
        {
            "label": item["label"],
            "point_value": round(
                _score_from_counts(matched_required_count, total_required, matched_preferred_count + 1, total_preferred, structure_score)
                - ats_score_float,
                1,
            ),
        }
        for item, match in zip(preferred_items, preferred_matches) if not match
    ]

    # Structural fixes first - a resume an ATS can't even parse (no
    # headers, no plain-text contact info) is a bigger problem than any
    # single missing keyword. Missing REQUIRED keywords are deliberately
    # NOT listed here (see missing_required_keywords below and the
    # docstring above) - those are real, answerable "do you have this"
    # gaps and belong in clarifying_questions, not a static bullet next to
    # ones that are just formatting edits.
    next_actions = []
    if "headers" in failed_checks:
        next_actions.append("Use standard section headers (e.g. PROFESSIONAL EXPERIENCE, EDUCATION, SKILLS) so ATS parsers recognize them.")
    if "markdown" in failed_checks:
        next_actions.append("Remove markdown formatting (**, #, tables, bullet symbols) - use plain text with '- ' dashes only.")
    if "dates" in failed_checks:
        next_actions.append("Use a consistent 'Month YYYY' date format throughout.")
    if "contact" in failed_checks:
        next_actions.append("Put contact info (email/phone) as plain text at the very top of the document.")

    remaining = max(0, 6 - len(next_actions))
    for item in missing_preferred[:remaining]:
        next_actions.append(f"Consider adding the preferred term \"{item['label']}\" if it genuinely applies.")

    if not next_actions and not missing_required:
        next_actions.append("Resume already covers the posting's extractable keywords and standard formatting well.")

    return {
        "ats_score": ats_score,
        "ats_rationale": rationale,
        "ats_next_actions": next_actions[:6],
        "missing_required_keywords": missing_required,
        "missing_preferred_keywords": missing_preferred,
        "matched_group_explanations": matched_group_explanations,
        "years_experience_equivalency_explanations": equivalency_explanations,
        "plateau_note": plateau_note_for_gaps(missing_required, matched_group_explanations),
    }


def detect_matched_keyword_regressions(
    required_keywords: list, preferred_keywords: list, old_resume_text: str | None, new_resume_text: str,
) -> list[str]:
    """Real, live-reproduced instance of CLAUDE.md's known failure pattern
    #2 (2026-08-09): every regenerate is a full rewrite of the resume
    text, so there is no guarantee a keyword the PREVIOUS draft matched
    survives into the new one - even when the new draft is objectively an
    improvement overall. Confirmed live on a real job (Upstream Bio):
    Zahir answered a gap question and clicked Generate; the net ATS score
    stayed flat (27/30 required both times) because the regenerate fixed
    "Engineering" while silently dropping a previously-present "life
    sciences" mention - a real regression completely invisible from the
    net score alone, which is exactly why net-score-only comparisons
    (like check_regenerate_impact's estimate) can't be the only signal.

    Returns the labels of any required/preferred keyword that WAS matched
    in old_resume_text and is NOT matched in new_resume_text - i.e. a
    keyword this rewrite specifically lost, not just one it never had.
    Deterministic text comparison, not an AI self-assessment (same
    "backstop, not a prompt" philosophy as every other keyword check in
    this module) - reuses this module's own scoring arithmetic on both
    texts rather than a separate diff heuristic. Returns [] when there's
    no prior text to compare against (a first-ever draft can't regress
    anything)."""
    if not old_resume_text:
        return []
    old_result = score_resume_against_keywords(required_keywords, preferred_keywords, old_resume_text)
    new_result = score_resume_against_keywords(required_keywords, preferred_keywords, new_resume_text)
    old_missing = {m["label"] for m in old_result["missing_required_keywords"] + old_result["missing_preferred_keywords"]}
    new_missing = {m["label"] for m in new_result["missing_required_keywords"] + new_result["missing_preferred_keywords"]}
    return sorted(new_missing - old_missing)


def detect_keyword_wording_regressions(
    old_required_keywords: list, old_preferred_keywords: list,
    new_required_keywords: list, new_preferred_keywords: list,
    resume_text: str,
) -> list[str]:
    """Sibling to detect_matched_keyword_regressions() above, but for the
    OTHER axis of the same problem (found live 2026-08-09, General
    force-re-extracting Zahir's real Upstream Bio job to pick up the
    2026-08-09 either/or fix): the RESUME TEXT stays exactly the same,
    but a fresh AI re-extraction call is free to reword an EXISTING,
    already-correct keyword slightly ("cloud-based infrastructure" ->
    "cloud infrastructure", "AI-enabled capabilities" -> "AI-enabled
    solutions") purely from ordinary AI non-determinism between calls.
    Since scoring is literal phrase matching, the reworded label no
    longer matches text that was matching fine under the old wording -
    the resume didn't get worse and neither extraction is "wrong" in
    isolation, but the net score silently drops with zero visible
    explanation of why, indistinguishable from a real regression unless
    someone diffs the two keyword lists by hand (which is exactly how
    this was first found - General noticed a fixed bug seemingly made
    the score go down).

    Deliberately does NOT try to auto-reconcile the wording (e.g. "if the
    new term is fuzzy-similar to an old one, keep the old spelling") -
    "cloud-based infrastructure" -> "cloud infrastructure" and "AI-enabled
    capabilities" -> "AI-enabled solutions" are both real examples where a
    naive similarity heuristic can't reliably tell cosmetic rewording
    apart from a genuine meaning change ("capabilities" vs "solutions" is
    not obviously synonymous), and this module's whole design philosophy
    is to never let a fuzzy judgment call silently decide what counts as
    a real skill match - see score_resume_against_keywords' own docstring
    and CLAUDE.md known failure pattern #3. Surfacing the change and
    letting a human judge is the honest version of a fix here, not
    inventing a second unreliable heuristic on top of the first.

    Returns the OLD labels of any required/preferred keyword that WAS
    matched against resume_text under the OLD keyword list and has
    disappeared entirely from the NEW list (not just "still present but
    now unmatched" - a genuinely brand-new keyword the re-extraction
    added that happens to be unmatched is an honest new gap, not a
    regression, so it's deliberately excluded here even though it also
    lowers the score). A flat OLD keyword that reappears as a member of a
    NEW any_of group (rather than its own flat entry) is correctly NOT
    flagged - it hasn't disappeared, it can still match exactly as
    before, just via a group structure instead (this matters in
    practice: the either/or generalization fix itself moves flat
    keywords into any_of groups on every re-extraction it touches, which
    would otherwise falsely show up as mass "regressions" here). Only
    OLD flat string keywords are ever reported as regressed, though -
    reconciling identity for an OLD any_of group itself (did this exact
    group get reworded, or genuinely change meaning?) is a fuzzier
    problem this doesn't attempt to solve."""
    def _all_present_labels(keywords):
        labels = set()
        for k in keywords:
            if isinstance(k, str):
                labels.add(k)
            elif isinstance(k, dict) and k.get("any_of"):
                labels.update(k["any_of"])
        return labels

    def _flat_labels(keywords):
        return {k for k in keywords if isinstance(k, str)}

    old_result = score_resume_against_keywords(old_required_keywords, old_preferred_keywords, resume_text)
    old_missing = {m["label"] for m in old_result["missing_required_keywords"] + old_result["missing_preferred_keywords"]}
    old_matched = _flat_labels(old_required_keywords + old_preferred_keywords) - old_missing
    new_labels = _all_present_labels(new_required_keywords + new_preferred_keywords)
    return sorted(old_matched - new_labels)
