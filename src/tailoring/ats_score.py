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
        "msc", "m.sc", "m.sc.", "ma", "m.a", "m.a.", "ms", "m.s", "m.s.",
        "mba", "m.b.a", "m.b.a.",
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
_CASE_SENSITIVE_ALIASES = {"it", "cs", "bsc", "ba", "bs", "ms", "ma", "mba", "phd"}


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
    if isinstance(item, dict) and item.get("any_of"):
        members = [str(m) for m in item["any_of"]]
        return {"label": " OR ".join(members), "members": members, "is_group": True}
    return {"label": str(item), "members": [str(item)], "is_group": False}


def _match_keyword_item(item: dict, text_lower: str, text_original: str) -> str | None:
    """Returns the specific member that satisfied this item (itself, for a
    plain string), or None if nothing in it matched."""
    for member in item["members"]:
        if _phrase_in_text(member.lower(), text_lower, text_original):
            return member
    return None


_STANDARD_HEADERS = (
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

    has_headers = sum(1 for h in _STANDARD_HEADERS if h in text_lower) >= 2
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


def _plateau_note(missing_required: list[dict], matched_group_explanations: list[str]) -> str | None:
    """Score-first-resume-flow spec item 2's second half: the system, not
    the user, should notice when the score has genuinely plateaued and say
    why in plain terms - rather than leaving Zahir to wonder "why did this
    go up/down/nowhere." None when there's nothing notable to explain
    (no remaining required gaps and no either/or group worth calling out)."""
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


def score_resume_against_keywords(
    required_keywords: list, preferred_keywords: list, resume_text: str,
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
    "plateau_note": str | None}. Always recomputed from the actual text
    passed in - the score moves when the text does, because this is
    literal keyword-overlap arithmetic plus formatting checks, not an
    independent AI guess.

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
    "answer", just an edit to make."""
    required_items = [_normalize_keyword_item(k) for k in required_keywords]
    preferred_items = [_normalize_keyword_item(k) for k in preferred_keywords]
    resume_lower = resume_text.lower()

    required_matches = [_match_keyword_item(item, resume_lower, resume_text) for item in required_items]
    preferred_matches = [_match_keyword_item(item, resume_lower, resume_text) for item in preferred_items]

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
        "plateau_note": _plateau_note(missing_required, matched_group_explanations),
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
