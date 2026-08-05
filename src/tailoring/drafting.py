"""Build step: one-click document drafting via a direct Anthropic API call
(Zahir's explicit choice 2026-07-30, over the alternative of a background
Claude Code task - see PRD §11 discussion). This is a deliberate exception
to "Python only orchestrates, Claude reasons live via a Claude Code
conversation" - every other judgment-requiring feature in this app (fit
score, LinkedIn scoring, Prospector Score, interview prep) still follows
that pattern. Drafting needed a synchronous in-app result, which only a
direct API call can give.

Reads the key from .env (ANTHROPIC_API_KEY), same pattern as
search/usajobs.py's USAJOBS_API_KEY. Requires Zahir to get his own key from
console.anthropic.com - this module never creates or manages the key itself.
"""

import json

import anthropic

from llm_client import (
    DEFAULT_MODEL,
    LLMCallFailed as DraftingFailed,
    LLMNotConfigured as DraftingNotConfigured,
    call_structured,
    call_with_web_search,
    get_client as _client,
    is_configured,
)
from tailoring.ats_score import score_resume_against_keywords, score_resume_ats

_RESUME_SPEC_COMMON = (
    "Tailored resume text for this specific job, written to be ATS-perfect - "
    "it must parse cleanly in an Applicant Tracking System, not just read "
    "well to a human. Plain text only - no markdown (no **, no #, no "
    "markdown bullets, no tables). Use standard, literal section headers "
    "(e.g. PROFESSIONAL EXPERIENCE, EDUCATION, SKILLS, CERTIFICATIONS) that "
    "ATS parsers recognize - not creative or merged headers. Reverse "
    "chronological order, consistent 'Month YYYY - Month YYYY' date format. "
    "Contact info as plain text at the very top, not in a table or header/"
    "footer. Naturally work in the exact keywords and skill terms the job "
    "posting itself uses (its required/preferred qualifications, tools, "
    "certifications) wherever the candidate genuinely has that experience - "
    "ATS systems match on literal keyword overlap, not paraphrases. Spell "
    "out acronyms at first use with the acronym in parentheses so both "
    "keyword matching and human readers catch it (e.g. 'Master Data "
    "Management (MDM)'). "
)

_RESUME_SPEC_TWO_PAGE_RULES = (
    "The WHOLE document must fit on roughly 2 "
    "pages - target a total of about 900-1100 words of body text (excluding "
    "headers/contact/dates), and treat these as hard caps, not suggestions: "
    "- Give full bullet-point detail to ONLY the 3 most recent roles: 5-6 "
    "bullets for the most recent, 4-5 for the 2nd most recent, 3-4 for the "
    "3rd most recent. "
    "- If two or more of those 3 recent roles are consecutive promotions at "
    "the SAME employer, list that employer's name/location ONCE, then list "
    "each title as its own sub-entry underneath with its own date range and "
    "bullets - do not repeat the full employer/location block per title. "
    "- Condense every role beyond the 3 most recent into a single "
    "'EARLIER CAREER' section: one line per role (title, employer, years, "
    "one short sentence of summary - no bullets at all), regardless of how "
    "many years ago it was. "
    "- Keep the Core Skills / Technical Skills section to a tight, "
    "scannable list of terms - not full sentences. "
    "Include a 'TARGET ROLE ALIGNMENT' section directly after the "
    "professional summary - at most 5 bullets, each mapping a specific "
    "requirement or theme from THIS job posting to the strongest genuine "
    "matching experience in the profile, in the posting's own language "
    "where accurate. Use plain '- ' dashes for bullet points, one blank "
    "line between sections. If applying these caps would still run "
    "noticeably over 2 pages, trim bullet wording and cut the least "
    "job-relevant bullets first - length wins over completeness here."
)

# USAJOBS itself hard-rejects any uploaded resume PDF over 2 pages at
# submission time (confirmed directly, 2026-08-03 - Zahir hit the real
# upload error on a federal-format draft that ran over). Federal resume
# convention (full chronological detail, hours/week, no page limit) is
# real practice for federal HR reviewers, but it's moot if USAJOBS won't
# accept the file - the platform's own hard limit wins. So USAJOBS
# postings now get the SAME 2-page hard cap as private-sector ones,
# just with federal-flavored content (hours/week per role, exact
# month/year dates) folded into that condensed structure wherever it
# still fits, instead of the old "no page limit" exception.
RESUME_SPEC_USAJOBS = (
    _RESUME_SPEC_COMMON
    + "This is a USAJOBS posting - USAJOBS itself rejects any uploaded "
    "resume PDF over 2 pages, so the federal 'no page limit' convention "
    "does not apply here; treat this exactly like the private-sector "
    "length rules below, with one federal-flavored addition: where space "
    "allows, note hours worked per week for the most recent 1-2 roles "
    "only (skip it entirely if it would push a bullet over length). "
    + _RESUME_SPEC_TWO_PAGE_RULES
)

RESUME_SPEC = (
    _RESUME_SPEC_COMMON
    + "If this posting is a US federal government job other than USAJOBS "
    "itself (an agency's own careers site, for instance) and that site "
    "states no page limit, follow full federal resume conventions: full "
    "chronological work history with month/year date ranges, hours worked "
    "per week, and specific, quantifiable accomplishments for each role. "
    "Otherwise (private-sector, retained-search, direct-company, or "
    "USAJOBS postings), the following hard caps apply: "
    + _RESUME_SPEC_TWO_PAGE_RULES
)


def _resume_spec_for_job(job: dict) -> str:
    if (job or {}).get("source") == "USAJOBS":
        return RESUME_SPEC_USAJOBS
    return RESUME_SPEC

DOC_SPECS = {
    "resume": RESUME_SPEC,
    "cover_letter": (
        "Cover letter body text only - no letterhead, no date, no address "
        "block, no 'Dear Hiring Manager' salutation formatting beyond a plain "
        "greeting line. Plain text, no markdown. 3-5 paragraphs explaining "
        "specifically why this candidate fits this role."
    ),
    "exec_bio": (
        "Third-person executive biography, roughly 150-250 words, suitable "
        "for a board packet or conference bio. Plain text, no markdown."
    ),
    "leadership_summary": (
        "First-person leadership philosophy / summary statement tailored to "
        "what this specific role and organization value, roughly 150-250 "
        "words. Plain text, no markdown."
    ),
    "apply_answers": (
        "A structured packet of ready-to-paste answers for the common, "
        "recurring fields on job-application forms/ATS systems (Workday, "
        "Greenhouse, iCIMS, etc.) - not a resume or letter. Each item is one "
        "field: a short label matching how ATS forms usually phrase it "
        "(e.g. 'Phone Number', 'LinkedIn URL', 'Earliest Start Date', "
        "'Desired Salary', 'Are you legally authorized to work in the "
        "United States?', 'How did you hear about this role?'). Cover "
        "standard contact fields (name, phone, email, address, LinkedIn "
        "URL) taken exactly from the profile, and common screening "
        "questions (work authorization, willingness to relocate/travel, "
        "years of experience in a relevant area, salary expectations, "
        "notice period, referral source). Never invent a fact the profile "
        "doesn't contain - if something like salary expectations or notice "
        "period was never captured, write '[Not yet provided - ask Zahir]' "
        "as the value instead of guessing."
    ),
}

SYSTEM_PROMPT = """You are drafting real job-application documents for a real candidate applying to a real job. Accuracy matters - these documents may be submitted as-is.

Ground rules:
- Only use employers, titles, dates, degrees, certifications, and accomplishments that are actually present in the candidate's master profile provided below. Never invent or embellish facts, metrics, employers, or credentials that aren't there.
- Reproduce every date and date range EXACTLY as given in the profile - same month and year, character for character. Never round, smooth, or shift a date to make a timeline look tidier or a transition look gapless - if the profile says a role started in March, write March, even if the prior role's stated end date is February of the same year.
- If the job posting calls for a qualification the profile doesn't clearly evidence, do not fabricate it - either omit it or honestly bridge from the closest real, transferable experience in the profile.
- Tailor every document specifically to this job posting and organization - reference the actual role, organization name, and what the posting emphasizes. Do not write generic, could-apply-to-any-job text.
- Return ONLY the documents requested via the structured output schema. No extra commentary.

Writing voice - under no circumstances may this read as AI-written. It must read as a real senior executive's own writing, full stop:
- British English prose conventions - phrasing, idiom, and a measured, understated register - but American spellings throughout (e.g. "color" not "colour", "organize" not "organise", "center" not "centre"), since this is for US employers.
- Vary sentence length and structure line to line; never fall into a uniform rhythm.
- Do not use these overused AI-writing tells: corporate buzzwords (leverage, spearhead, synergy, robust, cutting-edge, seamless, dynamic, passionate, game-changer); repetitive three-item lists; formulaic openers ("In today's fast-paced environment...", "I am thrilled to apply..."); "not just X, but Y" constructions; excessive em dashes; and stacking multiple adjectives before a noun.
- Every claim should sound like something this specific person would actually say about his own work - concrete, specific, a little understated rather than oversold.
- Before returning the text, silently re-read it as if you were an AI-detection reviewer looking for generated-text patterns (uniform bullet cadence, hollow superlatives, generic transition phrases). If anything reads as machine-generated, rewrite that line in plainer, more specific, human terms before returning it."""


SCORE_SYSTEM_PROMPT = """You are scoring how well one job posting fits this specific candidate, for their personal job-search tool. Read the candidate's master profile below and reason genuinely about fit - never a keyword count.

Consider:
- Seniority match: check the profile's self-reported seniority/experience level (a "seniority" field, if present) against what this posting expects - read what's actually in the profile, don't assume any particular level by default.
- Domain/functional match: does the posting's field genuinely align with the candidate's actual background per the profile, vs. an unrelated field - a senior-sounding title alone does not mean relevance.
- Non-US locations count against fit somewhat (relocation/visa impractical) unless the posting is explicitly remote.
- The profile's "gap_interview_answers" list may include entries with "is_disqualifier": true - real, candidate-confirmed exclusions (a role type they've explicitly said they don't consider themselves qualified for, or don't want, despite otherwise-matching experience - e.g. a candidate who rules out CISO-titled roles despite broader cybersecurity oversight experience). Score a posting matching any such entry LOW regardless of subject-matter proximity. Entries without that flag are just supporting facts, not exclusions - don't treat them as disqualifiers.
- Roles requiring current military/National Guard membership (e.g. "Title 32" postings) score 0 regardless of relevance - not eligible.
- Entry-level/intern/student/recent-graduate programs score 0 regardless of subject matter - wrong seniority.
- Staffing-firm or retained-executive-search-firm postings should be scored on the underlying role itself, same criteria as any direct posting.

Assign a 0-100 fit_score and a one-sentence, specific, plain-language fit_rationale explaining why (not generic filler) via the structured output schema."""


TARGET_ROLES_SYSTEM_PROMPT = """You are proposing a starter set of target job titles, plus the standard title-ladder/expectations checklist for one primary role/vertical, for a real job candidate - based on their actual resume text and their own stated target industries/verticals and seniority. This seeds an editable settings table the candidate reviews and adjusts themselves, never applied automatically - keep titles realistic to how they're actually posted for this trade and seniority level (not generic or padded), and never invent employers, credentials, or experience the resume doesn't support."""


def _target_roles_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "target_roles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "A job title this candidate should search/track, matching how it's commonly posted (not overly specific or invented).",
                        },
                        "priority_weight": {
                            "type": "integer",
                            "description": "0-10: how strongly to prioritize this title. 10 for the closest direct match to their current level and background, lower for adjacent/equivalent/stretch titles they may not have listed themselves.",
                        },
                    },
                    "required": ["name", "priority_weight"],
                    "additionalProperties": False,
                },
                "description": "5-12 target job titles for this candidate's chosen verticals, seniority, and resume background - include direct matches to their most recent title plus genuinely adjacent/equivalent titles they may not have thought to list themselves (the same spirit as a human recruiter suggesting related titles).",
            },
            "ladder_industry": {
                "type": "string",
                "description": "The single primary industry/vertical this title ladder is generated for - pick the best-fit one of the candidate's stated verticals if they listed several.",
            },
            "ladder_role": {
                "type": "string",
                "description": "The single primary role/title (usually the top-weighted target_roles entry) this title ladder describes.",
            },
            "title_ladder": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "skill": {
                            "type": "string",
                            "description": "A specific skill, credential, tool, or domain-specific knowledge area expected for this role in this vertical - matching the style of an experienced recruiter's checklist, not generic soft skills.",
                        },
                        "why_it_matters": {
                            "type": "string",
                            "description": "One sentence: why this specific item matters for this role/vertical.",
                        },
                    },
                    "required": ["skill", "why_it_matters"],
                    "additionalProperties": False,
                },
                "description": "8-15 specific, checkable skills/credentials/knowledge areas a strong candidate for ladder_role in ladder_industry would be expected to have - the standard title-ladder/expectations checklist for this trade, used later to spot real gaps in this candidate's resume. Never generic filler like 'communication skills'.",
            },
        },
        "required": ["target_roles", "ladder_industry", "ladder_role", "title_ladder"],
        "additionalProperties": False,
    }


def generate_target_roles(resume_text: str, industries: list[str], seniority: str, model: str | None = None) -> dict:
    """One reasoning call proposing a starter target_roles/weights list plus
    a title-ladder skills checklist for the candidate's primary vertical/
    role, from their resume text + stated target industries/verticals +
    self-reported seniority. Prefills the Settings tab's target-roles editor
    (the candidate reviews/edits before it's ever saved) and extends
    skills/lookup.py's role_skills.json with the generated ladder, so future
    gap-detection has real data for this vertical instead of only the
    original hand-built Lifesciences/Pharma entry. Returns
    {"target_roles": [...], "ladder_industry": str, "ladder_role": str,
    "title_ladder": [...]}. Raises DraftingNotConfigured/DraftingFailed same
    as the other drafting calls."""
    client = _client()
    content = (
        "CANDIDATE'S RESUME TEXT:\n" + resume_text +
        "\n\nCANDIDATE'S STATED TARGET INDUSTRIES/VERTICALS:\n" + "\n".join(industries) +
        "\n\nCANDIDATE'S SELF-REPORTED SENIORITY/EXPERIENCE:\n" + (seniority or "(not provided)")
    )
    data = call_structured(
        client,
        system=TARGET_ROLES_SYSTEM_PROMPT,
        user_content=content,
        schema=_target_roles_schema(),
        max_tokens=4000,
        model=model,
        effort="high",
        refusal_message="Claude declined to propose target roles. Try again.",
    )

    ladder_industry = data.get("ladder_industry")
    ladder_role = data.get("ladder_role")
    if not ladder_industry or not ladder_role:
        raise DraftingFailed("Claude's response was missing the expected ladder industry/role - try again.")

    from skills.lookup import save_role_skills
    save_role_skills(ladder_industry, ladder_role, data.get("title_ladder", []))

    return data


ATS_KEYWORDS_SYSTEM_PROMPT = """You are extracting the literal keywords/skills/tools/certifications a job posting itself asks for, for a deterministic ATS keyword-match scorer downstream - you are NOT scoring or judging fit, just pulling out real terms that are actually present in the posting's text.

Rules:
- Only extract terms that genuinely appear in the posting (as words/phrases or unmistakable synonyms of them, e.g. "Excel" for "Microsoft Excel") - never invent a skill the posting doesn't mention.
- required_keywords: terms from a "required"/"minimum qualifications"/"must-have" section, or stated as mandatory even without an explicit section header.
- preferred_keywords: terms from a "preferred"/"desired"/"nice-to-have"/"bonus" section, or that read as a plus rather than mandatory.
- If the posting doesn't clearly separate required vs preferred, use your best judgment on which items read as mandatory vs a plus - don't force a 50/50 split.
- Keep each term short (1-4 words) and in the posting's own wording - e.g. "SQL", "AWS", "Project Management", "Agile", "PMP certification" - not full sentences or restated requirements.
- If the posting text is boilerplate/empty/has no real requirements in it at all, return empty lists for both - do not pad with generic guesses."""


def _ats_keywords_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "required_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short (1-4 word) required/must-have terms taken directly from the posting's own wording. Empty list if none are genuinely stated.",
            },
            "preferred_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short (1-4 word) preferred/nice-to-have terms taken directly from the posting's own wording. Empty list if none are genuinely stated.",
            },
        },
        "required": ["required_keywords", "preferred_keywords"],
        "additionalProperties": False,
    }


def _extract_ats_keywords(client: "anthropic.Anthropic", job: dict, model: str | None = None) -> tuple[list[str], list[str]]:
    """One real-NLP-judgment AI call that pulls the literal required/
    preferred keyword list out of this job's own posting text (title +
    qualification_summary/description, whichever this job record has - see
    search/job_store.py). This is the piece regex heuristics can't do well
    (real language understanding of what's mandatory vs a nice-to-have in
    messy scraped prose) - everything downstream (the actual score) stays
    deterministic counting against this list, done by
    ats_score.score_resume_against_keywords(), not another AI guess.

    Cached on the job record (search.job_store.update_job_ats_keywords) so
    the same posting always scores against the same keyword list rather
    than re-extracting (and potentially drifting) on every regenerate -
    same caching shape as _lookup_company_address's organization_address.
    Returns ([], []) without caching on any drafting failure, so a
    transient API error doesn't permanently freeze a job at "no keywords
    found" - the caller falls back to the local heuristic for that one
    draft and the next regenerate gets another real attempt."""
    posting_text = "\n".join(filter(None, [
        job.get("title"), job.get("qualification_summary"), job.get("description"),
    ]))
    if not posting_text.strip():
        return [], []
    try:
        data = call_structured(
            client,
            system=ATS_KEYWORDS_SYSTEM_PROMPT,
            user_content=f"JOB POSTING:\n{posting_text}",
            schema=_ats_keywords_schema(),
            max_tokens=1500,
            model=model,
            effort="medium",
            refusal_message="Claude declined to extract ATS keywords.",
        )
    except (DraftingNotConfigured, DraftingFailed):
        return [], []

    required = data.get("required_keywords") or []
    preferred = data.get("preferred_keywords") or []

    from search.job_store import update_job_ats_keywords

    job["ats_required_keywords"] = required
    job["ats_preferred_keywords"] = preferred
    if job.get("source") and job.get("job_id"):
        update_job_ats_keywords(job["source"], job["job_id"], required, preferred)
    return required, preferred


def _score_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "fit_score": {"type": "integer", "description": "0-100 fit score for this candidate against this posting."},
            "fit_rationale": {"type": "string", "description": "One sentence, specific to this role, plain language."},
        },
        "required": ["fit_score", "fit_rationale"],
        "additionalProperties": False,
    }


def score_job(job: dict, profile: dict, model: str | None = None, on_progress=None) -> dict:
    """Live-scores a single job against the master profile via the direct
    API - for jobs that need an immediate score outside the daily scheduled
    task or a live Claude Code conversation (namely, jobs added manually via
    the Results tab intake form, which would otherwise sit invisible - the
    Results tab hides any job with no fit_score at all). Mirrors the exact
    rubric panga-daily-job-search's SKILL.md step 5 already uses, so scores
    stay comparable across the app rather than following a different rubric.
    Returns {"fit_score": int, "fit_rationale": str}. Raises
    DraftingNotConfigured/DraftingFailed same as generate_documents().

    If given, on_progress(substatus: str) is called with a live "thinking..."
    / "writing... (N characters so far)" status as the response streams in -
    same real-progress mechanism as _draft_one()'s document generation
    (Zahir's explicit ask 2026-07-31: no spinner anywhere should be opaque
    when the underlying call can report real progress instead)."""
    client = _client()
    content = (
        "JOB POSTING:\n" + json.dumps(job, indent=2, default=str) +
        "\n\nCANDIDATE'S MASTER PROFILE:\n" + json.dumps(profile, indent=2, default=str)
    )
    data = call_structured(
        client,
        system=SCORE_SYSTEM_PROMPT,
        user_content=content,
        schema=_score_schema(),
        max_tokens=2000,
        model=model,
        effort="high",
        on_progress=on_progress,
        refusal_message="Claude declined to score this job. Try again.",
    )
    return {"fit_score": data["fit_score"], "fit_rationale": data["fit_rationale"]}


def _schema(doc_keys: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {k: {"type": "string", "description": DOC_SPECS[k]} for k in doc_keys},
        "required": doc_keys,
        "additionalProperties": False,
    }


def _resume_schema(job: dict | None = None) -> dict:
    # The resume gets a richer schema than the other doc types: alongside
    # the text itself, it carries clarifying_questions for real facts that
    # would close a gap. ats_score/ats_rationale/ats_next_actions are
    # deliberately NOT part of this schema - they used to be a free-text
    # Claude self-assessment made in the same call that wrote the resume,
    # which is an independent, memoryless AI guess every time (no memory of
    # the previous score or what changed), and produced a score that never
    # moved no matter what was edited. tailoring.ats_score.score_resume_ats()
    # computes those three fields deterministically after this call returns,
    # from real keyword-overlap arithmetic against the drafted text - see
    # generate_documents() below.
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": _resume_spec_for_job(job)},
            "suggested_strategy_tag": {
                "type": "string",
                "description": (
                    "A short (3-6 word) hyphenated label for what's "
                    "distinctive about THIS specific draft's approach for "
                    "this posting - e.g. 'concise-2-page-ats-focused', "
                    "'leadership-narrative-emphasis', 'federal-format-"
                    "detailed'. This describes a real choice you made in "
                    "writing this draft, not a fact about the candidate - "
                    "state it directly, no hedging needed. Prefills the "
                    "app's own 'strategy tag' field; the candidate can "
                    "edit or clear it."
                ),
            },
            "clarifying_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["skill_gap", "disqualifier_check"],
                            "description": (
                                "\"skill_gap\" (the normal case) for a missing real "
                                "fact/number/name/date that would raise the ATS "
                                "score if confirmed. \"disqualifier_check\" only "
                                "when this posting's title/domain sits right at the "
                                "edge of what the profile's real experience covers, "
                                "in a way the candidate might want to hard-exclude "
                                "going forward (not just skip this one job) - the "
                                "same kind of thing as a candidate ruling out "
                                "CISO-titled roles despite broader cybersecurity "
                                "experience. Before proposing one, check whether the "
                                "profile's gap_interview_answers already has an "
                                "is_disqualifier entry covering this same role type "
                                "- if so, don't ask again, the scoring already "
                                "applies it."
                            ),
                        },
                        "skill": {
                            "type": "string",
                            "description": "Short label for this gap or disqualifier topic (e.g. 'SK Life Science IT team size/budget', or 'CISO-titled roles'), matching the master profile's gap-tracking convention.",
                        },
                        "question": {
                            "type": "string",
                            "description": (
                                "For skill_gap: a direct, specific question whose "
                                "answer is a genuine, checkable fact (a number, a "
                                "name, a date) that would close this gap - not "
                                "vague or stylistic. For disqualifier_check: a "
                                "direct yes/no-style question asking whether this "
                                "role type is one the candidate wants excluded from "
                                "future matches, or whether their experience "
                                "actually covers it after all."
                            ),
                        },
                        "suggested_answer": {
                            "type": "string",
                            "description": (
                                "For skill_gap: a proposed starting draft for the "
                                "answer, phrased as the candidate's own words - e.g. "
                                "a plausible number/scope guess given the role and "
                                "the rest of the profile ('Roughly 8-10 engineers, "
                                "~$2M budget?'). A suggestion to edit, never a "
                                "stated fact - make that uncertainty visible in the "
                                "phrasing itself (hedge words, a trailing '?') "
                                "rather than asserting it. Leave as an empty string "
                                "if you have no reasonable basis to propose "
                                "anything. For disqualifier_check: always an empty "
                                "string - this is a genuine judgment call only the "
                                "candidate can make, never a guess."
                            ),
                        },
                    },
                    "required": ["type", "skill", "question", "suggested_answer"],
                    "additionalProperties": False,
                },
                "description": (
                    "3-10 specific, directly answerable questions - either real "
                    "facts this resume is currently missing that would raise the "
                    "score if the candidate actually has them (skill_gap), or a "
                    "borderline-fit role type worth confirming as a standing "
                    "exclusion or not (disqualifier_check, rare - only when "
                    "genuinely borderline). Never invent an answer yourself, ask "
                    "instead; use however many genuine items actually exist "
                    "(sometimes only 3, up to 10), don't pad to hit a count. Skip "
                    "anything already answered elsewhere in the profile or that's "
                    "purely about wording/structure rather than a missing fact."
                ),
            },
        },
        "required": ["text", "suggested_strategy_tag", "clarifying_questions"],
        "additionalProperties": False,
    }


def _apply_answers_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "apply_answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Short field label, matching how ATS forms usually phrase it.",
                        },
                        "value": {
                            "type": "string",
                            "description": (
                                "Exact ready-to-paste answer text, or "
                                "'[Not yet provided - ask Zahir]' if the profile "
                                "doesn't contain this fact."
                            ),
                        },
                    },
                    "required": ["label", "value"],
                    "additionalProperties": False,
                },
                "description": DOC_SPECS["apply_answers"],
            },
        },
        "required": ["apply_answers"],
        "additionalProperties": False,
    }


def _questions_worth_asking(clarifying_questions: list[dict], ats_score: int) -> list[dict]:
    """Drops clarifying_questions once ats_score is already maxed at 100 -
    those questions come from the same drafting call as the resume text,
    before the deterministic ats_score even exists (see
    tailoring.ats_score.score_resume_against_keywords()/score_resume_ats()),
    so the model drafting them has no way to know the real score can't go
    any higher. A "fact that would raise the score" is meaningless once
    there's nothing left to raise (Zahir, 2026-08-04: was still seeing
    these on maxed-score jobs - confusing, pointless)."""
    if ats_score >= 100:
        return []
    return clarifying_questions


def _draft_one(
    client: "anthropic.Anthropic",
    shared_context: list[dict],
    doc_key: str,
    model: str | None,
    on_progress=None,
    doc_index: int = 1,
    doc_total: int = 1,
    job: dict | None = None,
):
    if doc_key == "resume":
        schema = _resume_schema(job)
    elif doc_key == "apply_answers":
        schema = _apply_answers_schema()
    else:
        schema = _schema([doc_key])
    # The resume schema carries the text itself plus suggested_strategy_tag/
    # clarifying_questions, and federal-format resumes alone can run 3000+
    # tokens - give it real headroom rather than truncating (hit for real
    # during testing at 6000 with a federal-length resume).
    max_tokens = 20000 if doc_key == "resume" else 6000

    def _progress(substatus):
        if on_progress:
            on_progress(doc_index, doc_total, doc_key, substatus)

    data = call_structured(
        client,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        user_content=shared_context + [{"type": "text", "text": f"\n\nDraft: {doc_key}"}],
        schema=schema,
        max_tokens=max_tokens,
        model=model,
        effort="high",
        on_progress=_progress if on_progress else None,
        refusal_message=(
            "Claude declined to draft this document. This is unusual for resume "
            "content - try again, or check the job posting text for anything unusual."
        ),
    )

    if doc_key == "resume":
        resume_text = data.get("text", "")
        job = job or {}
        required_kw = job.get("ats_required_keywords")
        preferred_kw = job.get("ats_preferred_keywords")
        if required_kw is not None and preferred_kw is not None:
            # AI-extracted keyword list already cached on the job record
            # (generate_documents() ensures this before calling _draft_one) -
            # the real-NLP-judgment path, see _extract_ats_keywords().
            ats = score_resume_against_keywords(required_kw, preferred_kw, resume_text)
        else:
            # Extraction was never attempted/failed for this job (e.g. no
            # posting text, or a transient API error) - fall back to the
            # dependency-free regex heuristic rather than leaving the
            # resume unscored.
            posting_text = "\n".join(filter(None, [
                job.get("title"), job.get("qualification_summary"), job.get("description"),
            ]))
            ats = score_resume_ats(posting_text, resume_text)
        return {
            "text": resume_text,
            "suggested_strategy_tag": data.get("suggested_strategy_tag", ""),
            "ats_score": ats["ats_score"],
            "ats_rationale": ats["ats_rationale"],
            "ats_next_actions": ats["ats_next_actions"],
            "clarifying_questions": _questions_worth_asking(data.get("clarifying_questions", []), ats["ats_score"]),
        }
    if doc_key == "apply_answers":
        return data.get("apply_answers", [])
    return data.get(doc_key, "")


def _lookup_company_address(client: "anthropic.Anthropic", organization: str, location: str | None) -> str | None:
    """Looks up an organization's real mailing/headquarters address via the
    Claude API's server-side web search tool, for the cover letter's
    recipient block - never guessed, only used if a real source turns up.
    Returns None on no confident match (docx_export.py's existing
    "[Company Address]" placeholder is the fallback for that case)."""
    location_hint = f" (job is located in {location})" if location else ""
    text, _cost = call_with_web_search(
        client,
        system=(
            "Look up one company's real, current mailing or headquarters "
            "address for a cover letter's recipient block. Search the web "
            "and confirm it from the company's own site or another "
            "reliable source - never guess or infer from the company's "
            "name/industry alone. Reply with ONLY the address as 1-3 "
            "short lines (street; city, state zip; country if not US) - "
            "no company name, no commentary. If you cannot find a "
            "confident, verifiable address, reply with exactly: NOT_FOUND"
        ),
        user_content=f"Company: {organization}{location_hint}",
        max_tokens=300,
        max_uses=3,
    )
    if not text or text == "NOT_FOUND" or len(text) > 300:
        return None
    return text


def generate_documents(
    job: dict,
    profile: dict,
    doc_keys: list[str],
    model: str | None = None,
    on_progress=None,
) -> dict:
    """Drafts real, tailored document text for exactly the requested doc_keys
    (subset of "resume", "cover_letter", "exec_bio", "leadership_summary",
    "apply_answers"), one API call per document type so progress is real,
    not simulated. The
    job+profile context is identical across those calls and marked
    cacheable, so only the first call pays full price for it - subsequent
    ones in the same batch read it back at ~10% cost.
    If given, on_progress(i, total, doc_key, substatus=None) is called right
    before drafting starts on the i-th (1-indexed) of `total` documents
    (substatus=None), then repeatedly during that document's own generation
    with a live sub-status ("thinking...", "writing... (N characters so
    far)") as the response streams in.
    Returns {doc_key: drafted_text}, except "resume" maps to {"text": ...,
    "suggested_strategy_tag": str, "ats_score": int, "ats_rationale": str,
    "ats_next_actions": [...], "clarifying_questions": [{"skill": ...,
    "question": ..., "suggested_answer": ...}]} instead of a
    plain string. ats_score/ats_rationale/ats_next_actions are computed
    deterministically by tailoring.ats_score.score_resume_ats() from real
    keyword-overlap arithmetic between the posting's own text (title +
    qualification_summary/description, whichever this job record has - see
    search/job_store.py) and the resume text Claude just wrote, right after
    this call returns - not asked of the same API call that drafted the
    text, which used to be an independent AI guess every time with no real
    comparison happening and a score that never moved no matter what
    changed. clarifying_questions are gaps Claude couldn't close honestly
    without more real facts (never invented; ask, don't fabricate - see
    profile/interview.py's save_answer(), the same mechanism this feeds
    back into via the Results tab). "apply_answers"
    maps to a list of {"label": ..., "value": ...} dicts (a ready-to-paste
    packet for common ATS form fields) rather than a single string. Raises
    DraftingNotConfigured if no API key is set, DraftingFailed on
    refusal/truncation/API error.

    When "cover_letter" is requested and this job hasn't been searched for
    an address before, also looks up the organization's real mailing
    address via a one-time web search (_lookup_company_address) and caches
    it onto the job record (search.job_store.update_job_address) plus the
    passed-in `job` dict in place, so callers building the cover letter's
    .docx (app.py, dossier.py) can read job["organization_address"]
    immediately after this call returns. Never falls back to a guessed
    address - an unconfirmed lookup caches "" (searched, not found) so it
    isn't re-searched every regenerate, and docx_export.py's own
    "[Company Address]" placeholder covers that case."""
    if not doc_keys:
        return {}

    client = _client()
    if "cover_letter" in doc_keys and "organization_address" not in job and job.get("organization"):
        from search.job_store import update_job_address

        address = _lookup_company_address(client, job["organization"], job.get("location")) or ""
        job["organization_address"] = address
        update_job_address(job.get("source"), job.get("job_id"), address)

    if "resume" in doc_keys and job.get("ats_required_keywords") is None:
        _extract_ats_keywords(client, job, model)

    shared_context = [{
        "type": "text",
        "text": (
            "JOB POSTING:\n" + json.dumps(job, indent=2, default=str) +
            "\n\nCANDIDATE'S MASTER PROFILE:\n" + json.dumps(profile, indent=2, default=str)
        ),
        "cache_control": {"type": "ephemeral"},
    }]

    results = {}
    total = len(doc_keys)
    for i, doc_key in enumerate(doc_keys, start=1):
        if on_progress:
            on_progress(i, total, doc_key)
        results[doc_key] = _draft_one(client, shared_context, doc_key, model, on_progress, i, total, job=job)
    return results


def save_gap_answers(job: dict, answered_questions: list[dict]) -> None:
    """Persists confirmed answers to a resume's clarifying_questions into the
    master profile's gap_interview_answers - the same store/shape
    profile/interview.py's save_answer() already writes to, so a fact
    confirmed here (e.g. "SK Life Science IT team size/budget") becomes
    available to every future job's drafting, not just this one.
    answered_questions is the subset of clarifying_questions the candidate
    actually typed a real answer for (blank ones already filtered out by the
    caller), each a {"skill":, "type":, "answer":} dict - "type" ==
    "disqualifier_check" is saved with is_disqualifier=True so
    SCORE_SYSTEM_PROMPT applies it to every future job, not just this one;
    anything else (including missing/old-shape entries) saves as an ordinary
    skill-gap fact. Never called with an invented answer - the Results tab
    UI only calls this with what the candidate actually typed."""
    from datetime import date

    from profile.interview import save_answer

    role_context = f"{job.get('title', 'Unknown role')} at {job.get('organization', 'Unknown organization')}"
    today = date.today().isoformat()
    for q in answered_questions:
        answer = (q.get("answer") or "").strip()
        if not answer:
            continue
        save_answer(
            skill=q["skill"], role_context=role_context, answer=answer, date_captured=today,
            is_disqualifier=(q.get("type") == "disqualifier_check"),
        )
