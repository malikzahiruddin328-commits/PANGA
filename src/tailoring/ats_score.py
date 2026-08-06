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


def _phrase_in_text(phrase: str, text_lower: str) -> bool:
    if re.match(r"^[\w\s]+$", phrase):
        pattern = r"\b" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"\b"
        return re.search(pattern, text_lower) is not None
    return phrase in text_lower


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


def score_resume_against_keywords(
    required_keywords: list[str], preferred_keywords: list[str], resume_text: str,
) -> dict:
    """The real, deterministic ATS score for one drafted resume against an
    already-extracted required/preferred keyword list (lowercase or not -
    matching is case-insensitive). Returns {"ats_score": int 0-100,
    "ats_rationale": str, "ats_next_actions": [str, ...],
    "missing_required_keywords": [str, ...]}. Always recomputed from the
    actual text passed in - the score moves when the text does, because
    this is literal keyword-overlap arithmetic plus formatting checks, not
    an independent AI guess.

    missing_required_keywords is returned separately from ats_next_actions
    (2026-08-06, real gap Zahir hit live): these are the specific,
    answerable "does the candidate actually have this" gaps, and
    drafting.py merges them into the resume's real clarifying_questions
    flow (the same interactive, savable mechanism Profile Gaps already
    uses) rather than leaving them as inert bullet-point text with no way
    to answer or act on them - see
    tailoring.drafting._merge_keyword_gap_questions(). ats_next_actions
    keeps only what's genuinely just informational/directive (structural
    formatting fixes, optional preferred-keyword suggestions) - things
    there's no real fact to "answer", just an edit to make."""
    required = [k.lower() for k in required_keywords]
    preferred = [k.lower() for k in preferred_keywords]
    resume_lower = resume_text.lower()

    matched_required = [k for k in required if _phrase_in_text(k, resume_lower)]
    matched_preferred = [k for k in preferred if _phrase_in_text(k, resume_lower)]

    required_coverage = (len(matched_required) / len(required)) if required else None
    preferred_coverage = (len(matched_preferred) / len(preferred)) if preferred else None

    if required_coverage is not None and preferred_coverage is not None:
        keyword_score = 100.0 * (0.75 * required_coverage + 0.25 * preferred_coverage)
    elif required_coverage is not None:
        keyword_score = 100.0 * required_coverage
    elif preferred_coverage is not None:
        keyword_score = 100.0 * preferred_coverage
    else:
        keyword_score = None

    structure_score, failed_checks = _structure_score(resume_text)

    if keyword_score is None:
        ats_score = round(structure_score)
    else:
        ats_score = round(0.75 * keyword_score + 0.25 * structure_score)
    ats_score = max(0, min(100, ats_score))

    total_kw = len(required) + len(preferred)
    matched_kw = len(matched_required) + len(matched_preferred)
    if total_kw:
        rationale = (
            f"Matched {matched_kw}/{total_kw} keywords/skills extracted from the "
            f"posting ({len(matched_required)}/{len(required)} required, "
            f"{len(matched_preferred)}/{len(preferred)} preferred); "
            f"formatting/structure check {round(structure_score)}%."
        )
    else:
        rationale = (
            "This posting's stored text didn't have distinct extractable "
            f"requirements - score reflects resume structure/formatting only "
            f"({round(structure_score)}%)."
        )

    missing_required = [k for k in required if k not in matched_required]
    missing_preferred = [k for k in preferred if k not in matched_preferred]

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
    for term in missing_preferred[:remaining]:
        next_actions.append(f"Consider adding the preferred term \"{term}\" if it genuinely applies.")

    if not next_actions and not missing_required:
        next_actions.append("Resume already covers the posting's extractable keywords and standard formatting well.")

    return {
        "ats_score": ats_score,
        "ats_rationale": rationale,
        "ats_next_actions": next_actions[:6],
        "missing_required_keywords": missing_required,
    }
