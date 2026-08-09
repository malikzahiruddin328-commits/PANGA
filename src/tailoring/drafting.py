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
import re

import anthropic

from llm_client import (
    DEFAULT_MODEL,
    LLMCallFailed as DraftingFailed,
    LLMNotConfigured as DraftingNotConfigured,
    LLMResponseTruncated,
    call_structured,
    call_with_web_search,
    get_client as _client,
    is_configured,
)
from skill_label_match import normalize_skill_label, skills_match
from tailoring.ats_score import plateau_note_for_gaps, score_resume_against_keywords, score_resume_ats
from tailoring.baseline_resume import select_baseline_resume_text

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
    "Management (MDM)'). Some roles in the master profile carry a "
    "parenthetical seniority-equivalence note after the title (e.g. 'Head "
    "of IT (CIO-equivalent)'). First judge THIS job posting's own "
    "seniority level (also reflected in the target_seniority_at_least_vp "
    "field below - keep the two consistent). If the posting itself is "
    "VP-level or higher, print the parenthetical in FULL exactly as stored "
    "(e.g. 'Head of IT (CIO-equivalent)') - it supports the case that the "
    "candidate has already operated at that level. If the posting is "
    "below VP-level, drop the parenthetical and print just the working "
    "title (e.g. 'Head of IT', NOT 'Head of IT (CIO-equivalent)') - for a "
    "role below that level, keeping the higher-rank qualifier on the "
    "printed title reads as applying beneath your level. Either way, "
    "dropping or keeping this parenthetical is not inventing or "
    "embellishing - it is the same underlying role, employer, dates, and "
    "bullet content either way, exactly as given. Separately, some roles "
    "also carry a leading rank-prefix ahead of the working title (e.g. "
    "the master profile's title field literally reads 'Vice President, "
    "Head of Applications') - write that title in FULL, prefix included, "
    "regardless of this posting's seniority; the app strips that prefix "
    "automatically for a below-VP posting based on the "
    "target_seniority_at_least_vp field, so don't try to handle it "
    "yourself in the text. "
    "Actively cross-reference the posting's own required/preferred vocabulary "
    "against the candidate's ENTIRE master profile before treating a term as "
    "uncovered - real gap Zahir hit live 2026-08-09: a posting's preferred "
    "term 'commercial scale readiness' was left as a passive suggestion "
    "('consider adding this if it genuinely applies') even though the "
    "profile already had a real, on-point fact supporting it (a product "
    "grown from under $500K to $1B in annual sales at a past employer) - the "
    "connection was there to find, but nothing looked for it before falling "
    "back to a suggestion Zahir would have to notice and act on himself, for "
    "every single job, every single time. For each such term, actively check "
    "whether ANY real fact elsewhere in the profile genuinely demonstrates "
    "it (not just the bullets you'd already planned to write) - a past "
    "outcome, a scope detail, a program result - even if the profile itself "
    "never uses the posting's exact phrase. If a real fact genuinely "
    "supports the term, weave it into a bullet using language closer to the "
    "posting's own wording (e.g. a revenue-growth story becomes evidence of "
    "'commercial scale readiness' if that is genuinely what it shows) - "
    "this is honest re-phrasing of an already-true fact for a closer "
    "keyword match, not the invented-guess exception below, so it needs no "
    "'?' hedge. Only do this when the fact GENUINELY, plainly supports the "
    "term without stretching its meaning - if nothing in the profile really "
    "demonstrates it, leave it uncovered rather than forcing a connection "
    "that overstates what the fact actually shows; the deterministic score "
    "will still surface it as a real gap for Zahir to consider separately. "
    "Zahir's explicit, deliberate exception to 'never invent or embellish': "
    "when a specific fact (a number, a name, a scope detail) would "
    "genuinely strengthen a bullet and there's real contextual basis for a "
    "plausible guess (the same standard as this app's own keyword-gap "
    "suggested answers - e.g. the term or something close to it already "
    "appears elsewhere in the profile), you may write that guess directly "
    "into the resume text with a trailing '?' marking it as unconfirmed "
    "(e.g. 'Led a team of 8-10 engineers?', 'Reduced incident response "
    "time by roughly 30%?') - same hedge-marker convention already used "
    "for suggested_answer guesses elsewhere in this app. Never guess a "
    "fact with NO real basis at all (an employer, a title, a date, or a "
    "number with nothing in the profile even loosely suggesting it) - "
    "omit it instead. This app enforces a hard, code-level rule that no "
    "document ever leaves with an unresolved '?' still in it, so a hedge "
    "marker here is safe to use, not a shortcut around honesty - it is "
    "exactly what tells the app and the candidate this specific claim "
    "still needs a real answer before it's final. Every fact you write "
    "this way MUST also appear in the unconfirmed_claims field below, "
    "using the EXACT SAME wording as it appears in resume_text, so the "
    "app can find and resolve it - never leave a '?' in resume_text "
    "without a matching unconfirmed_claims entry."
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
    "Do NOT give the job-to-experience alignment content its own separate "
    "'TARGET ROLE ALIGNMENT' section or any other standalone bold-caps "
    "header for it (real problem hit 2026-08-06: a job-application "
    "portal's own auto-parser expects the first employer entry right after "
    "the summary, saw a header-styled section there instead, and parsed "
    "its content straight into the Company field of Work Experience 1 - "
    "it is not a standard ATS-recognized resume section, so it must not be "
    "styled to look like one). Instead, fold this content directly into "
    "the PROFESSIONAL SUMMARY as its closing part - either woven into the "
    "summary's prose or as a short run of at most 5 plain '- ' bullets "
    "immediately under the summary paragraph (still inside that same "
    "section, no new header), each mapping a specific requirement or theme "
    "from THIS job posting to the strongest genuine matching experience in "
    "the profile, in the posting's own language where accurate. Use plain "
    "'- ' dashes for any bullet points, one blank line between sections. "
    "If applying these caps would still run "
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
        purpose="target_roles",
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
- If the posting text is boilerplate/empty/has no real requirements in it at all, return empty lists for both - do not pad with generic guesses.

Do NOT extract these three categories - a real gap caught live 2026-08-07: a candidate with 25+ years of exactly this kind of experience was asked "do you have real, genuine experience with it?" for things that were never checkable skill gaps in the first place, just the extractor pulling the wrong category of thing out of the posting entirely:
- Years-of-experience thresholds. This is a numeric tenure requirement, not a checkable skill/tool/fact - e.g. do NOT extract "8+ years", "10+ years IT leadership", "5 years executive technology" as a keyword (the ATS score's own structural checks already cover tenure elsewhere; this extractor is for discrete skills/tools/certifications only).
- Alternate-title lists. When a posting lists acceptable prior job titles (e.g. "IT director, solutions architect, technology consultant, or similar role"), that's the posting describing what kind of role the candidate should have held - not separate skills each needing individual proof. Do NOT extract "IT director", "solutions architect", or "technology consultant" as individual keywords from a title-list phrase like this.
- Generic soft-skill/leadership phrases with no single checkable term. e.g. do NOT extract "executive presence", "presentation", "c-suite stakeholders", "strong communication skills", "executive technology strategies" - these are vague qualities almost any senior resume already demonstrates narratively; there's no literal fact to add for them.

Either/or qualification groups (score-first-resume-flow spec, item 2): a JD often phrases a requirement as a substitutable alternative - e.g. "Master's degree, OR Bachelor's degree plus 8+ years of experience," or "PMP certification or equivalent project management experience." Extract this as ONE item shaped {"any_of": [alternative1, alternative2, ...]} instead of flat independent keywords for each side - a candidate who satisfies either alternative has satisfied the whole requirement, and extracting the alternatives as separate flat keywords would falsely flag a real, satisfied requirement as a missing gap the moment the candidate takes the other branch. Each alternative in any_of MUST be a single, atomic, short (1-4 word) term in the posting's own wording, same as any other keyword - e.g. "Bachelor's degree", not "Associate's or Bachelor's Degree". If one side of the posting's own either/or itself lists multiple sub-options (e.g. "Associate's or Bachelor's Degree plus experience"), split those into separate atomic members of any_of too (e.g. "Associate's degree", "Bachelor's degree") rather than bundling them into one combined alternative string - a bundled alternative can't be matched against a candidate's real, differently-worded resume text downstream. Only use this shape when the posting genuinely states an "or"/substitutable relationship between two or more concrete alternatives - not for an ordinary list of several required skills, which stays flat.

Degree-field lists (a different shape from the either/or above, real gap caught live 2026-08-09 on a real posting): a JD often states one degree LEVEL, then lists several acceptable FIELDS of study for it, sometimes closed with an open-ended "or a related field" - e.g. "Bachelor's degree in Information Technology, Computer Science, Engineering, or a related field required." Here the degree level ("Bachelor's degree") is its own separate, ordinary required keyword, extracted normally - but the list of fields is a SEPARATE any_of group of its own, e.g. {"any_of": ["Information Technology", "Computer Science", "Engineering"]}, satisfied if the candidate's resume shows a degree in ANY ONE of the named fields. Do NOT extract each named field as its own independent flat required keyword - a candidate whose real field is Engineering must never be separately dinged for not also holding Computer Science, when the posting itself already treats them as interchangeable. Use the field name alone as each alternative (e.g. "Computer Science"), never the full "Bachelor's degree in Computer Science" phrase - a candidate's resume states a field ("B.S. Computer Science", "Computer Science") without ever literally repeating "degree in", so a bundled alternative would never match real resume wording, same reasoning as the either/or rule above. Do NOT add "a related field" itself as a literal alternative - it is an open-ended catch-all with no fixed term to ever match against real text, not a real keyword; leave it unextracted rather than fabricating a literal match for a field the posting never actually named."""


# Deterministic backstop for the years-of-experience category above - a
# regex is easy to catch here and shouldn't depend on the prompt being
# followed perfectly every time (same lesson as the rank-prefix and
# keyword-synonym fixes earlier this week: prompt-only guidance for this
# extraction pipeline has already proven unreliable on its own more than
# once). Matches when the WHOLE extracted keyword starts with a
# number-of-years pattern, e.g. "8+ years", "10+ years IT leadership",
# "5 years executive technology" - the tenure threshold is the entire
# point of the string, not a discrete skill/tool/certification worth a
# literal keyword-match check.
#
# Corrected 2026-08-09 (Mirror's audit flagged the original 2026-08-07
# commit's title - "Stop ATS keyword extraction from pulling tenure/
# titles/soft-skills" - as reading like all three categories got a real
# fix, when only this one did; the code comment here was already honest
# about the gap, but CLAUDE.md's own retrospective wasn't). Generic
# soft-skill phrases now ALSO get a real deterministic backstop below
# (_drop_generic_soft_skill_keywords) - a curated deny-list, not a regex,
# since these are known fixed phrases rather than a number pattern.
# Alternate-title lists genuinely still have none: the giveaway that
# makes "IT director" an alternate-title mention rather than a real
# required title ("...or similar role" trailing the list) lives in the
# posting's surrounding prose, which no longer exists once "IT director"
# has already been extracted as a bare string - there's nothing left in
# the extracted keyword itself for a regex or deny-list to key off, so
# this one category is still prompt-reliant alone, not solved.
_YEARS_EXPERIENCE_KEYWORD_RE = re.compile(r"^\d+\+?\s*years?\b", re.IGNORECASE)

# Curated, deliberately narrow deny-list for the generic-soft-skill
# category - the exact phrases ATS_KEYWORDS_SYSTEM_PROMPT already names
# as never-extract, plus their most common real-world variants. Matched
# via normalize_skill_label's plain case/punctuation-insensitive equality
# (NOT skills_match's looser phrase-containment) - a deny-list has to be
# exact-ish, or it risks dropping a real keyword that happens to contain
# a denied phrase as a whole word (e.g. "Presentation Layer Architecture"
# containing "presentation").
_GENERIC_SOFT_SKILL_PHRASES = {
    "executive presence",
    "presentation",
    "presentation skills",
    "c suite stakeholders",
    "strong communication skills",
    "excellent communication skills",
    "communication skills",
    "executive technology strategies",
    "strategic thinking",
    "results driven",
    "results oriented",
    "team player",
    "stakeholder management",
    "cross functional leadership",
    "leadership skills",
    "interpersonal skills",
    "problem solving skills",
    "analytical skills",
    "attention to detail",
    "self starter",
    "fast paced environment",
}


def _drop_years_experience_keywords(keywords: list) -> list:
    # Group items ({"any_of": [...]}) pass through untouched - a
    # years-of-experience threshold wouldn't sensibly appear as one side
    # of a real either/or group, and .strip() would crash on a dict.
    return [k for k in keywords if not (isinstance(k, str) and _YEARS_EXPERIENCE_KEYWORD_RE.match(k.strip()))]


def _drop_generic_soft_skill_keywords(keywords: list) -> list:
    return [
        k for k in keywords
        if not (isinstance(k, str) and normalize_skill_label(k) in _GENERIC_SOFT_SKILL_PHRASES)
    ]


# Deterministic backstop for the degree-field-list rule above (2026-08-09,
# real gap caught live on a real posting): even though the prompt tells
# the model to keep each any_of alternative to the bare field name (e.g.
# "Computer Science", not "Bachelor's degree in Computer Science" - a
# bundled phrase like that would never match a real resume, which states
# a field without ever literally repeating "degree in"), prompt
# compliance alone isn't trusted for this (same "backstop, not just an
# instruction" bar as every other keyword-extraction fix this week).
# Strips a leading "<degree level> [degree] in " prefix from every flat
# keyword and every any_of member, so even a bundled phrase the model
# still emits gets canonicalized down to just the field name before
# scoring ever sees it.
_DEGREE_IN_PREFIX_RE = re.compile(
    r"^(?:bachelor'?s?|master'?s?|associate'?s?|doctoral|doctorate|ph\.?d\.?)\s*(?:degree\s+)?in\s+",
    re.IGNORECASE,
)


def _strip_degree_in_prefix(term: str) -> str:
    return _DEGREE_IN_PREFIX_RE.sub("", term.strip()).strip()


def _strip_degree_in_prefix_keywords(keywords: list) -> list:
    result = []
    for k in keywords:
        if isinstance(k, str):
            result.append(_strip_degree_in_prefix(k))
        elif isinstance(k, dict) and k.get("any_of"):
            result.append({"any_of": [_strip_degree_in_prefix(str(m)) for m in k["any_of"]]})
        else:
            result.append(k)
    return result


# Anthropic's structured-output schema rejects "oneOf" (confirmed live,
# 2026-08-08: "Schema type 'oneOf' is not supported") - "anyOf" is the
# supported equivalent for "this array item is either a plain string or an
# either/or group object", used here for required_keywords/
# preferred_keywords per the either/or extraction rule above. Also
# confirmed live: "minItems" values other than 0 or 1 aren't supported
# either, so "at least two real alternatives" is prompt guidance only
# (the either/or rule above already says so), not schema-enforced.
_KEYWORD_ITEM_SCHEMA = {
    "anyOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {"any_of": {"type": "array", "items": {"type": "string"}}},
            "required": ["any_of"],
            "additionalProperties": False,
        },
    ]
}


def _ats_keywords_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "required_keywords": {
                "type": "array",
                "items": _KEYWORD_ITEM_SCHEMA,
                "description": (
                    "Short (1-4 word) required/must-have terms taken directly from the posting's own wording, "
                    "or {\"any_of\": [...]} for a substitutable either/or requirement (see the either/or rule "
                    "above). Empty list if none are genuinely stated."
                ),
            },
            "preferred_keywords": {
                "type": "array",
                "items": _KEYWORD_ITEM_SCHEMA,
                "description": (
                    "Short (1-4 word) preferred/nice-to-have terms taken directly from the posting's own "
                    "wording, or {\"any_of\": [...]} for a substitutable either/or preference. Empty list if "
                    "none are genuinely stated."
                ),
            },
        },
        "required": ["required_keywords", "preferred_keywords"],
        "additionalProperties": False,
    }


def _extract_ats_keywords(client: "anthropic.Anthropic", job: dict, model: str | None = None) -> tuple[list, list]:
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
    job_key = (job["source"], job["job_id"]) if job.get("source") and job.get("job_id") else None
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
            purpose="ats_keyword_extraction",
            job_key=job_key,
        )
    except (DraftingNotConfigured, DraftingFailed):
        return [], []

    required = _strip_degree_in_prefix_keywords(_drop_generic_soft_skill_keywords(_drop_years_experience_keywords(data.get("required_keywords") or [])))
    preferred = _strip_degree_in_prefix_keywords(_drop_generic_soft_skill_keywords(_drop_years_experience_keywords(data.get("preferred_keywords") or [])))

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
    job_key = (job["source"], job["job_id"]) if job.get("source") and job.get("job_id") else None
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
        purpose="fit_score",
        job_key=job_key,
    )
    return {"fit_score": data["fit_score"], "fit_rationale": data["fit_rationale"]}


def _schema(doc_keys: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {k: {"type": "string", "description": DOC_SPECS[k]} for k in doc_keys},
        "required": doc_keys,
        "additionalProperties": False,
    }


_CLARIFYING_QUESTION_ITEM_SCHEMA = {
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
            "target_seniority_at_least_vp": {
                "type": "boolean",
                "description": (
                    "True if THIS specific job posting is itself at the VP "
                    "level or higher (VP, SVP, EVP, President, C-suite/"
                    "Chief-*-Officer titles); False for anything below that "
                    "(Director, Head of, Senior Manager, individual-"
                    "contributor roles, etc.). Real gap found 2026-08-06: "
                    "asking the model to also strip a leading rank-prefix "
                    "like 'Vice President,' from a past title in the text "
                    "itself was unreliable - it kept the prefix on a "
                    "below-VP posting in testing even with explicit "
                    "instructions. This field lets the app do that "
                    "stripping deterministically instead - keep it "
                    "consistent with how you actually wrote the text (the "
                    "parenthetical seniority-equivalence notes above)."
                ),
            },
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
                "items": _CLARIFYING_QUESTION_ITEM_SCHEMA,
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
            "unconfirmed_claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "skill": {
                            "type": "string",
                            "description": "Short label for what this unconfirmed guess is about, matching the master profile's gap-tracking convention (same style as clarifying_questions' skill field).",
                        },
                        "text": {
                            "type": "string",
                            "description": "The EXACT text you wrote in resume_text for this guess, trailing '?' included, character for character - used to find and resolve it later.",
                        },
                    },
                    "required": ["skill", "text"],
                    "additionalProperties": False,
                },
                "description": (
                    "One entry for EVERY hedged, unconfirmed guess written into "
                    "resume_text with a trailing '?' (see the resume-writing "
                    "rules above) - empty list if you didn't write any. Every "
                    "'?' in resume_text must have a matching entry here; never "
                    "leave one unaccounted for."
                ),
            },
        },
        "required": [
            "text", "target_seniority_at_least_vp", "suggested_strategy_tag",
            "clarifying_questions", "unconfirmed_claims",
        ],
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


def _suggested_answer_for_keyword_gap(term: str, profile: dict | None) -> str:
    """A genuine, honest starting guess for a missing-required-keyword
    question - Zahir's explicit correction 2026-08-06: an empty box was the
    wrong call even here. He'd rather see *something* to react to and edit
    than compose an answer from scratch, same "accept or edit" bar as every
    other suggested_answer in this app - it just has to stay honestly
    hedged, never asserted as fact, same as those.

    Cheap, deterministic, no AI call: if the term (or a close variant)
    appears anywhere in the candidate's full profile - not just the resume
    text this specific draft produced, which the deterministic scorer
    already confirmed doesn't mention it - that's a genuine, real signal
    worth surfacing (the profile may cover experience this particular
    tailored resume didn't happen to include). If it doesn't appear
    anywhere at all, there is truly no real basis to guess yes or no, so
    the honest starting text says exactly that rather than inventing
    confidence - still real text to edit, not a blank box."""
    profile_text_lower = json.dumps(profile or {}, default=str).lower()
    if term.lower() in profile_text_lower:
        return (
            f"Your profile may already mention \"{term}\" - can you confirm "
            "and briefly describe your real experience with it?"
        )
    return "Unknown - please describe your real experience (if any) with this."


def _merge_keyword_gap_questions(
    clarifying_questions: list[dict], missing_required_keywords: list[dict],
    previously_answered_skills: list[str] | None = None,
    profile: dict | None = None,
    missing_preferred_keywords: list[dict] | None = None,
) -> list[dict]:
    """Folds missing-required-keyword gaps into the same clarifying_questions
    structure Profile Gaps already uses, instead of leaving them as inert
    "How to raise it" bullet text with no way to answer or act on it (real
    gap Zahir hit live 2026-08-06: ats_next_actions and clarifying_questions
    were built as two disconnected systems - one a static list, the other
    the real interactive, savable, drafts-feeding mechanism - and "add
    Databricks" belonged in the second one, not sitting inert in the first).

    Each missing required keyword becomes a real skill_gap question -
    answerable, persisted via save_gap_answers() into the master profile's
    gap_interview_answers exactly like every other skill_gap question, and
    actually read back into the next regenerate. suggested_answer comes from
    _suggested_answer_for_keyword_gap() - a real, honestly-hedged starting
    guess (Zahir, 2026-08-06: even "unknown, please fill in" beats a blank
    box - something to react to and edit, not compose from scratch).

    Deduped two ways:
    - Against the AI-generated clarifying_questions passed in (via
      skill_label_match.skills_match against each existing question's own
      "skill" label - normalized equality or a real word-boundary-
      respecting phrase match, not a bare substring) - the AI may have
      already asked about the same skill in its own words this same round.
      Real gap flagged by Mirror 2026-08-08: bare bidirectional substring
      containment ("x in y or y in x") is the same class of bug as this
      week's proven "it"-pronoun/BSc case-sensitivity fixes in
      ats_score.py - a short label like "IT" is a bare substring of
      plenty of unrelated words ("credit", "legitimate"), which could
      wrongly suppress a genuinely distinct question.
    - Against previously_answered_skills (pass profile["gap_interview_answers"]'s
      skill labels) - without this, a keyword the candidate already said
      "no, I don't have that" to would keep coming back as a "new" question
      on every single regenerate forever, since the deterministic keyword
      match has no memory of its own and the resume text will never
      naturally gain a skill the candidate confirmed they don't have. This
      is the same profile/interview.py._already_answered() precedent this
      module's own gap-detection already follows - a real answer (even a
      "no") means don't ask again, not just a real yes.

    missing_required_keywords is now a list of {"label": str,
    "point_value": float} dicts (score-first-resume-flow spec item 2,
    2026-08-08) - label may itself read as an either/or group ("Master's
    degree OR Bachelor's degree plus 8+ years experience") when
    ats_score.py's either/or matching found no satisfying alternative;
    point_value carries through onto the generated question so callers can
    show the real, scorer-computed value of answering it, without
    re-deriving that arithmetic themselves.

    missing_preferred_keywords (2026-08-09, real gap General caught Zahir
    hit live) is the same shape as missing_required_keywords, used only to
    backfill point_value onto the AI's OWN free-form clarifying_questions
    (the "which CTMS product," "how many IT staff" kind, generated by the
    same drafting call as the resume text) when one happens to correspond
    to a real missing keyword the deterministic scorer already knows the
    value of - matched via skills_match against that question's own
    "skill" label, same non-fragile matcher as every other dedup in this
    function, never a bare substring. A free-form question with no
    corresponding keyword at all (asking for a fact like team size/budget
    that was never one of the job's extracted keywords) has no
    deterministic point value to attach - point_value stays None for those
    rather than a guess, and the UI is expected to say so honestly rather
    than silently omitting the badge."""
    already_asked = [q.get("skill") or "" for q in clarifying_questions if q.get("skill")]
    already_asked += [s for s in (previously_answered_skills or []) if s]
    keyword_gap_lookup = list(missing_required_keywords) + list(missing_preferred_keywords or [])
    merged = []
    for q in clarifying_questions:
        # Real bug found live (2026-08-09, surfaced by app.py adding an
        # st.rerun() right after save_gap_answers() - see that call site's
        # comment): this loop passed every pre-existing clarifying_question
        # through unconditionally, never checking previously_answered_skills
        # - only the missing_required_keywords loop below did. That's the
        # OPPOSITE of what render_answered_gap_questions's own docstring
        # already claims ("_merge_keyword_gap_questions's profile-history
        # check stop re-asking it") - a question stored on
        # resume_clarifying_questions at draft time never actually dropped
        # off just because it was genuinely answered afterward, only a full
        # regenerate ever cleared it. Harmless before (the stale "still
        # open" display just sat there until an unrelated rerun), but with
        # the same skill re-appearing in open_questions every render, a
        # fresh rerun right after saving re-renders the identical
        # already-answered text_area, which still differs from its
        # suggested_answer, saving and rerunning again forever.
        if q.get("skill") and any(skills_match(q["skill"], s) for s in (previously_answered_skills or [])):
            continue
        match = None
        if q.get("point_value") is None and q.get("skill"):
            match = next(
                (item for item in keyword_gap_lookup if skills_match(item["label"], q["skill"])),
                None,
            )
        # Only copy (breaking object identity) when there's actually a
        # real value to backfill - callers that pass an existing question
        # through untouched (the common case: most free-form questions
        # have no corresponding keyword at all) get the exact same object
        # back, not an unnecessary clone.
        merged.append({**q, "point_value": match["point_value"]} if match is not None else q)
    for item in missing_required_keywords:
        term = item["label"]
        if any(skills_match(term, skill) for skill in already_asked):
            continue
        merged.append({
            "type": "skill_gap",
            "skill": term,
            "question": (
                f"The posting requires \"{term}\" - do you have real, genuine "
                "experience with it? If so, briefly describe it so it can be "
                "added to your resume."
            ),
            "suggested_answer": _suggested_answer_for_keyword_gap(term, profile),
            "point_value": item["point_value"],
        })
    return merged


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


# Matches a rank-prefix leading a past title line, e.g. "Vice President, "
# or "SVP, " ahead of "Head of Applications" - a pattern, not a literal
# string tied to this one profile's exact wording (real gap found live
# 2026-08-06: relying on the model to drop this itself was unreliable even
# with explicit instructions naming the exact string - it kept the prefix
# on a below-VP posting twice in a row). Anchored to the start of a line
# (title lines are always alone on their own line in this app's resume
# format) so it can't accidentally eat a mid-sentence "President, " inside
# real bullet prose.
_RANK_PREFIX_RE = re.compile(
    r"^(\s*)(?:Executive\s+Vice\s+President|Senior\s+Vice\s+President|"
    r"Vice\s+President|EVP|SVP|VP|President)\s*,\s*",
    re.IGNORECASE,
)
# Matches a trailing seniority-equivalence parenthetical on a title line,
# e.g. "(CIO-equivalent)" or "(Chief Information Officer equivalent)" -
# narrow on purpose (requires the word "equivalent" inside the
# parenthesis) so it can't accidentally eat an unrelated parenthetical
# elsewhere in the text. Live-verified 2026-08-07: unlike the rank-prefix
# above, the model DID reliably drop this on its own in earlier runs - but
# a later run of the exact same below-VP posting kept it anyway, so this
# needs the same deterministic backstop, not just better prompt wording a
# second time.
_SENIORITY_PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\bequivalent\b[^)]*\)", re.IGNORECASE)


def _strip_rank_prefixes(resume_text: str) -> str:
    """Deterministic safety net for a below-VP target posting: strips a
    leading rank-prefix from any line that starts with one, and any
    trailing seniority-equivalence parenthetical anywhere in the text -
    same pattern as docx_export.py's all-caps-name normalizer. The model
    still makes the genuinely contextual judgment call (is this posting
    VP-level or not, via target_seniority_at_least_vp), but once that call
    is made, removing a known qualifier pattern is mechanical and
    shouldn't depend on the model reliably doing it in the text every
    single time - real gap found live 2026-08-06/07: both forms were
    caught NOT being dropped on a below-VP posting in separate live runs,
    despite explicit prompt instructions for each."""
    lines = resume_text.split("\n")
    lines = [_RANK_PREFIX_RE.sub(r"\1", line) for line in lines]
    lines = [_SENIORITY_PARENTHETICAL_RE.sub("", line) for line in lines]
    return "\n".join(lines)


_CROSS_DOCUMENT_CONSISTENCY_DOC_KEYS = {"cover_letter", "exec_bio", "leadership_summary"}


def _resume_consistency_block(resume_text: str) -> dict:
    # Real gap Zahir hit live 2026-08-09: cover_letter/exec_bio/leadership_
    # summary each get their own independent API call sharing only the raw
    # job+profile context, never the resume text drafted earlier in the
    # SAME batch (or already on file for a "regenerate just this one doc"
    # case) - so each one independently re-interprets the same profile
    # facts with real room to phrase, round, or emphasize something
    # differently. A reviewer who spots a number/date/achievement stated
    # differently across two of Zahir's own documents reads that as an
    # inconsistency (a red flag), not cosmetic drift - even though every
    # individual document is independently accurate against the profile.
    return {
        "type": "text",
        "text": (
            "\n\nTHE RESUME ALREADY WRITTEN FOR THIS CANDIDATE FOR THIS SAME "
            "JOB:\n" + resume_text + "\n\nStay factually consistent with it: "
            "any concrete fact you state here (a number, a date, a company "
            "name, a specific achievement) that's also stated in the resume "
            "above must actually agree with it, not just coincidentally "
            "happen to - go back and re-derive it from the resume's own "
            "wording rather than independently re-deriving your own version "
            "from the raw profile. This document can legitimately emphasize "
            "different things than the resume (a different angle, a "
            "different level of detail, facts the resume didn't have room "
            "for) - it does not need to repeat the same facts, but whatever "
            "facts it DOES state must not contradict what the resume "
            "already says. If the resume text above contains a hedged, "
            "unconfirmed guess marked with a trailing '?', do not state "
            "that same fact here as settled/confirmed without the hedge - "
            "either leave it out, or mark it with the same '?' if you "
            "reference it at all. Stating a genuinely unresolved claim as "
            "confirmed fact in a second document is a worse version of the "
            "same inconsistency, not a safer one."
        ),
    }


def _draft_one(
    client: "anthropic.Anthropic",
    shared_context: list[dict],
    doc_key: str,
    model: str | None,
    on_progress=None,
    doc_index: int = 1,
    doc_total: int = 1,
    job: dict | None = None,
    profile: dict | None = None,
    resume_text_for_consistency: str | None = None,
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

    job_key = (job["source"], job["job_id"]) if job and job.get("source") and job.get("job_id") else None
    user_content = list(shared_context)
    if doc_key in _CROSS_DOCUMENT_CONSISTENCY_DOC_KEYS and resume_text_for_consistency:
        user_content.append(_resume_consistency_block(resume_text_for_consistency))
    user_content.append({"type": "text", "text": f"\n\nDraft: {doc_key}"})
    data = call_structured(
        client,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        user_content=user_content,
        schema=schema,
        max_tokens=max_tokens,
        model=model,
        effort="high",
        on_progress=_progress if on_progress else None,
        refusal_message=(
            "Claude declined to draft this document. This is unusual for resume "
            "content - try again, or check the job posting text for anything unusual."
        ),
        purpose=f"draft_{doc_key}",
        job_key=job_key,
    )

    if doc_key == "resume":
        resume_text = data.get("text", "")
        if not data.get("target_seniority_at_least_vp", True):
            # Deterministic safety net, not left to prompt compliance alone
            # (see _strip_rank_prefixes docstring) - only for a below-VP
            # posting; defaults to True (no stripping) if the field is
            # somehow missing, so an absent judgment fails toward leaving
            # the text untouched rather than silently mangling a title.
            resume_text = _strip_rank_prefixes(resume_text)
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
        previously_answered_skills = [a.get("skill") for a in (profile or {}).get("gap_interview_answers", [])]
        merged_questions = _merge_keyword_gap_questions(
            data.get("clarifying_questions", []), ats.get("missing_required_keywords", []),
            previously_answered_skills, profile,
            missing_preferred_keywords=ats.get("missing_preferred_keywords", []),
        )
        return {
            "text": resume_text,
            "suggested_strategy_tag": data.get("suggested_strategy_tag", ""),
            "ats_score": ats["ats_score"],
            "ats_rationale": ats["ats_rationale"],
            "ats_next_actions": ats["ats_next_actions"],
            "clarifying_questions": _questions_worth_asking(merged_questions, ats["ats_score"]),
            "ats_plateau_note": ats.get("plateau_note"),
            "unconfirmed_claims": data.get("unconfirmed_claims", []),
        }
    if doc_key == "apply_answers":
        return data.get("apply_answers", [])
    return data.get(doc_key, "")


def _lookup_company_address(
    client: "anthropic.Anthropic", organization: str, location: str | None,
    job_key: tuple[str, str] | None = None,
) -> str | None:
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
        purpose="company_address_lookup",
        job_key=job_key,
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
    existing_resume_text: str | None = None,
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
    "[Company Address]" placeholder covers that case.

    existing_resume_text (2026-08-09): when doc_keys doesn't include
    "resume" (a "regenerate just the other documents" case - e.g. a job
    already has a drafted resume and only the cover letter is being
    redrafted), pass the job's current resume_text here so cover_letter/
    exec_bio/leadership_summary can still be checked for factual
    consistency against it - see _resume_consistency_block(). When
    "resume" IS in doc_keys, the freshly-drafted resume text is used
    automatically instead (doc_keys already guarantees "resume" is
    processed first when present - see ui/app.py's doc_types ordering),
    so this param is only needed for the resume-not-in-this-batch case."""
    if not doc_keys:
        return {}

    client = _client()
    job_key = (job["source"], job["job_id"]) if job.get("source") and job.get("job_id") else None
    if "cover_letter" in doc_keys and "organization_address" not in job and job.get("organization"):
        from search.job_store import update_job_address

        address = _lookup_company_address(client, job["organization"], job.get("location"), job_key) or ""
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
    resume_text_for_consistency = existing_resume_text
    for i, doc_key in enumerate(doc_keys, start=1):
        if on_progress:
            on_progress(i, total, doc_key)
        results[doc_key] = _draft_one(
            client, shared_context, doc_key, model, on_progress, i, total,
            job=job, profile=profile, resume_text_for_consistency=resume_text_for_consistency,
        )
        if doc_key == "resume":
            fresh_resume = results["resume"]
            resume_text_for_consistency = fresh_resume["text"] if isinstance(fresh_resume, dict) else fresh_resume
    return results


def analyze_fit_before_drafting(job: dict, profile: dict, app_record: dict) -> dict:
    """Score-first-resume-flow spec, Step 1 ("analyze fit" - no document
    written): composes the real backend pieces (baseline selection - item
    1, either/or-aware deterministic scoring with point values - item 2,
    keyword-gap-to-clarifying-question folding) into the exact contract
    UI refinement's Step 1/2 screen (render_analyze_fit_section) already
    renders against. Was tailoring.score_first_flow_stub's stand-in until
    2026-08-08, when items 1/2/4 landed for real - see that file's
    (deleted) module docstring for the swap history.

    UI refinement's current scope only calls this once a resume already
    exists for the job (render_analyze_fit_section's own docstring - the
    pre-first-draft case is a natural next step once this lands, not
    included here to keep this swap itself low-risk). Baseline is
    therefore this job's OWN current drafted resume text, scored fresh
    against its cached keyword lists - not select_baseline_resume_text()
    (baseline_resume.py deliberately excludes a job's own resume from its
    own candidate pool; that function is for the "no resume for THIS job
    yet" case). Falls back to select_baseline_resume_text() anyway when
    app_record somehow has no resume_text, so this degrades gracefully
    rather than crashing if that scope ever does expand.

    Returns {"projected_score": int, "projected_rationale": str,
    "baseline_source": str, "plateau_note": str | None,
    "open_questions": [...], "answer_more_exhausted_message": str | None} -
    open_questions is the SAME clarifying_questions shape
    render_analyze_fit_section already consumes (type/skill/question/
    suggested_answer/point_value), folding missing_required_keywords in
    via _merge_keyword_gap_questions rather than leaving them as inert
    "how to raise it" text.

    projected_score is a REAL projection, not just this job's current
    drafted-resume score (real gap Zahir hit live 2026-08-09, General):
    baseline_text only changes when "Generate" actually rewrites it, so a
    naive re-score of the same unchanged text can never move no matter how
    many Step-1 questions get answered - answering a question only saves
    the fact via save_gap_answers(), it doesn't touch resume_text. Once a
    fact is genuinely confirmed, _merge_keyword_gap_questions already
    drops the matching question out of open_questions (so it isn't asked
    again) - but the real point_value that fact would add was being
    dropped right along with it instead of counted. This adds it back: any
    missing_required_keywords/missing_preferred_keywords entry whose label
    matches an already-answered profile skill (via skills_match, same
    matcher as every other dedup here) is a confirmed-but-not-yet-drafted
    fact, and its point_value (the SAME number score_resume_against_keywords
    already computed, not a separate guess) is added to the baseline
    score, capped at 100 - exactly what a fresh Generate would produce
    once that fact lands in the actual text. plateau_note is likewise
    recomputed against only the gaps that are STILL genuinely open (not
    yet confirmed), via ats_score.plateau_note_for_gaps - the raw
    score_result's own plateau_note would otherwise keep calling an
    already-answered gap "real, not a phrasing issue," which stops being
    true the moment the candidate answers it."""
    baseline_text = app_record.get("resume_text")
    baseline_source = "your current drafted resume for this job"
    if not baseline_text:
        baseline_text, baseline_source = select_baseline_resume_text(job)

    required_keywords = job.get("ats_required_keywords") or []
    preferred_keywords = job.get("ats_preferred_keywords") or []
    score_result = score_resume_against_keywords(required_keywords, preferred_keywords, baseline_text)

    previously_answered_skills = [a["skill"] for a in profile.get("gap_interview_answers", []) if a.get("skill")]

    def _already_confirmed(item: dict) -> bool:
        return any(skills_match(item["label"], skill) for skill in previously_answered_skills)

    missing_required = score_result["missing_required_keywords"]
    missing_preferred = score_result["missing_preferred_keywords"]
    confirmed_required = [item for item in missing_required if _already_confirmed(item)]
    confirmed_preferred = [item for item in missing_preferred if _already_confirmed(item)]
    still_open_required = [item for item in missing_required if item not in confirmed_required]

    confirmed_bonus = sum(item["point_value"] for item in confirmed_required + confirmed_preferred)
    projected_score = max(0, min(100, round(score_result["ats_score"] + confirmed_bonus)))

    projected_rationale = score_result["ats_rationale"]
    if confirmed_bonus:
        confirmed_count = len(confirmed_required) + len(confirmed_preferred)
        projected_rationale += (
            f" Includes +{confirmed_bonus:g} pt(s) for {confirmed_count} already-confirmed "
            "answer(s) not yet reflected in this draft - Generate to bake them in for real."
        )

    plateau_note = plateau_note_for_gaps(still_open_required, score_result["matched_group_explanations"])

    merged_questions = _merge_keyword_gap_questions(
        app_record.get("resume_clarifying_questions") or [],
        missing_required,
        previously_answered_skills=previously_answered_skills,
        profile=profile,
        missing_preferred_keywords=missing_preferred,
    )
    open_questions = _questions_worth_asking(merged_questions, projected_score)

    return {
        "projected_score": projected_score,
        "projected_rationale": projected_rationale,
        "baseline_source": baseline_source,
        "plateau_note": plateau_note,
        "open_questions": open_questions,
        "answer_more_exhausted_message": (
            "No more real gaps found based on your current profile." if not open_questions else None
        ),
    }


_ANSWER_MORE_SYSTEM_PROMPT = (
    "You already produced an initial set of resume clarifying_questions for "
    "this job and candidate. The candidate clicked \"Answer more questions\" "
    "and wants to know if there is ANYTHING further genuinely worth asking, "
    "beyond what's already been asked or is already covered by a "
    "deterministic keyword-gap check the app runs separately.\n\n"
    "You will be given the job posting, the candidate's full master profile "
    "(including every fact already confirmed via gap_interview_answers), "
    "and an ALREADY COVERED list - topics from the last drafted resume's own "
    "clarifying_questions, from already-confirmed profile answers, and from "
    "keyword gaps the app's own deterministic scorer already tracks and "
    "presents on its own. Do not repeat ANY of these, even reworded or "
    "phrased differently - if a topic is on that list, it's handled.\n\n"
    "Return 0-10 NEW, genuinely distinct, checkable facts (skill_gap) or "
    "borderline-fit judgment calls (disqualifier_check) that would help if "
    "confirmed - same bar as the original set: a real fact/number/name/date, "
    "never invented, never vague or stylistic. If there is truly nothing "
    "further worth asking, return an EMPTY list - do not invent a low-value "
    "question just to have something to show; an honest empty answer is "
    "correct and expected once the real gaps are exhausted."
)


# Real crash found by RM's live-fire test against Zahir's actual profile
# (2026-08-09): the prompt embeds the CANDIDATE'S MASTER PROFILE in full
# (~98,000 characters on real data, not job-specific - every job's prompt
# carries the same full profile), and a fixed max_tokens=3000 with no
# retry truncated on essentially the first real call, not an edge case.
# Same escalate-on-genuine-truncation pattern job_alert_reasoning.py's
# extract_listings() already proved out against a comparably real-world-
# large input (a bundled digest email) - not a per-input formula (the
# real digests needing the most tokens weren't reliably the ones with the
# most input either), just retry with more budget only when the response
# actually came back cut off.
_ANSWER_MORE_MAX_TOKENS_TIERS = [8000, 16000, 32000]


def _answer_more_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "clarifying_questions": {
                "type": "array",
                "items": _CLARIFYING_QUESTION_ITEM_SCHEMA,
                "description": (
                    "0-10 genuinely NEW questions not already covered by the "
                    "ALREADY COVERED list - an empty list is a valid, honest "
                    "answer when nothing further is genuinely worth asking."
                ),
            },
        },
        "required": ["clarifying_questions"],
        "additionalProperties": False,
    }


def request_additional_gap_questions(
    job: dict, profile: dict, app_record: dict, model: str | None = None, on_progress=None,
) -> dict:
    """Score-first-resume-flow spec item 5: clicking "Answer more questions"
    is supposed to trigger a real new round of AI question generation given
    everything already asked/confirmed - not just re-show whatever
    open_questions already happened to be. Real gap Zahir hit live
    2026-08-09 (General, watching his session): the button was a bare
    st.rerun() with no actual generation call behind it at all, so
    open_questions just stayed whatever it already was, and the already-
    built "no more real gaps found" honest message (answer_more_exhausted_
    message above) could only ever fire from the deterministic keyword-gap
    side happening to already be empty - never from genuinely asking the AI
    for more and it coming back with nothing.

    Returns {"added_count": int, "new_questions": [...],
    "merged_clarifying_questions": [...]} - added_count is 0 when the AI
    call itself legitimately found nothing further (the caller shows the
    honest exhausted message); merged_clarifying_questions is
    app_record's existing resume_clarifying_questions with new_questions
    appended, ready for the caller to persist via upsert_application - this
    function itself does not write to applications.json, matching this
    module's existing boundary (save_gap_answers writes to the profile
    store only; app.py owns every applications.json write).

    Deduped the same non-fragile way as every other gap-question merge in
    this module (skills_match, not a bare substring) against: this job's
    already-stored resume_clarifying_questions, every skill already
    confirmed anywhere in the profile, and every keyword the deterministic
    scorer already tracks as a real gap. The AI is EXPLICITLY told not to
    repeat any of these, but per this codebase's own established pattern
    (CLAUDE.md known failure pattern #3 - prompt compliance alone isn't
    trusted for anything checked downstream by a deterministic rule),
    anything that slips through the prompt anyway is filtered out here
    too, not just asked not to happen.

    Escalates max_tokens across _ANSWER_MORE_MAX_TOKENS_TIERS if the
    response comes back genuinely truncated (LLMResponseTruncated) - real
    crash found by RM's live-fire test 2026-08-09: a real master profile
    is large enough (~98,000 characters) that a fixed, low max_tokens
    truncated on essentially the first real call, not an edge case. Raises
    LLMCallFailed (of which LLMResponseTruncated is a subclass, so an
    `except DraftingFailed`/`except LLMCallFailed` catches either) if even
    the largest tier still truncates, or on any other failure - those
    aren't retried, since a bigger token budget wouldn't fix a refusal or
    invalid JSON."""
    client = _client()
    required_keywords = job.get("ats_required_keywords") or []
    preferred_keywords = job.get("ats_preferred_keywords") or []
    resume_text = app_record.get("resume_text") or ""
    score_result = score_resume_against_keywords(required_keywords, preferred_keywords, resume_text)

    existing_questions = app_record.get("resume_clarifying_questions") or []
    already_covered = (
        [q["skill"] for q in existing_questions if q.get("skill")]
        + [a["skill"] for a in profile.get("gap_interview_answers", []) if a.get("skill")]
        + [
            item["label"]
            for item in score_result["missing_required_keywords"] + score_result["missing_preferred_keywords"]
        ]
    )

    job_key = (job["source"], job["job_id"]) if job.get("source") and job.get("job_id") else None
    content = (
        "JOB POSTING:\n" + json.dumps(job, indent=2, default=str) +
        "\n\nCANDIDATE'S MASTER PROFILE:\n" + json.dumps(profile, indent=2, default=str) +
        "\n\nALREADY COVERED (do not repeat any of these, even reworded):\n" +
        json.dumps(sorted(set(already_covered)), indent=2)
    )
    for max_tokens in _ANSWER_MORE_MAX_TOKENS_TIERS:
        try:
            data = call_structured(
                client,
                system=_ANSWER_MORE_SYSTEM_PROMPT,
                user_content=content,
                schema=_answer_more_schema(),
                max_tokens=max_tokens,
                model=model,
                effort="high",
                on_progress=on_progress,
                refusal_message="Claude declined to check for more questions. Try again.",
                purpose="answer_more_gap_questions",
                job_key=job_key,
            )
        except LLMResponseTruncated:
            if max_tokens == _ANSWER_MORE_MAX_TOKENS_TIERS[-1]:
                raise
            continue
        break

    new_questions = [
        q for q in data.get("clarifying_questions", [])
        if q.get("skill") and not any(skills_match(q["skill"], covered) for covered in already_covered)
    ]
    return {
        "added_count": len(new_questions),
        "new_questions": new_questions,
        "merged_clarifying_questions": existing_questions + new_questions,
    }


def check_regenerate_impact(job: dict, app_record: dict, profile: dict) -> dict:
    """Score-first-resume-flow spec item 6: when "Generate" is clicked
    again for a job that already has a resume, tells the caller (UI
    refinement's confirmation popup) whether there's genuinely new
    confirmed info to incorporate, and the real numbers to show either
    way - never a UI-side approximation of arithmetic this module already
    owns.

    Returns {"has_new_info": bool, "new_fact_count": int,
    "current_score": int, "estimated_new_score": int | None,
    "cost_estimate": float | None, "last_generation_cost": float | None}.
    estimated_new_score/cost_estimate are only meaningful when
    has_new_info is True; last_generation_cost only when it's False.

    "New" is determined by comparing each gap_interview_answers entry's
    date_captured (a date, not a precise timestamp) against this
    application's documents_drafted_at date - there's no existing link
    between "which specific answers were used for this job's last draft,"
    so this is the best available real signal without new data plumbing,
    not perfect same-day precision. estimated_new_score reuses the exact
    point_value already computed for this job's stored
    resume_clarifying_questions (score_resume_against_keywords' own
    formula, score-first-resume-flow item 2) rather than a separate guess,
    matched to a newly-answered skill by exact case-insensitive label
    equality - by construction, save_gap_answers() saves a question's
    "skill" field back verbatim, so this isn't the same fragile
    AI-vs-deterministic substring matching Mirror flagged elsewhere
    (2026-08-08) - it's comparing a string to its own origin."""
    from cost_log import last_cost_for_job

    current_score = app_record.get("resume_ats_score") or 0
    drafted_at = app_record.get("documents_drafted_at")
    drafted_date = drafted_at[:10] if drafted_at else None

    new_answers = [
        a for a in profile.get("gap_interview_answers", [])
        if a.get("date_captured") and (drafted_date is None or a["date_captured"] >= drafted_date)
    ]
    has_new_info = bool(new_answers)

    source, job_id = job.get("source"), job.get("job_id")
    last_cost = last_cost_for_job(source, job_id, purpose="draft_resume") if source and job_id else None

    if not has_new_info:
        return {
            "has_new_info": False, "new_fact_count": 0, "current_score": current_score,
            "estimated_new_score": None, "cost_estimate": None, "last_generation_cost": last_cost,
        }

    new_skills_lower = {a["skill"].lower() for a in new_answers if a.get("skill")}
    point_values = [
        q["point_value"] for q in (app_record.get("resume_clarifying_questions") or [])
        if q.get("point_value") and (q.get("skill") or "").lower() in new_skills_lower
    ]
    estimated_new_score = min(100, round(current_score + sum(point_values)))

    return {
        "has_new_info": True,
        "new_fact_count": len(new_answers),
        "current_score": current_score,
        "estimated_new_score": estimated_new_score,
        # Best available forward-looking estimate: the real logged cost of
        # this job's last actual resume draft, not a fresh pricing guess -
        # a redraft's real cost depends on final resume length, which
        # isn't knowable before the call happens.
        "cost_estimate": last_cost,
        "last_generation_cost": None,
    }


def save_gap_answers(job: dict, answered_questions: list[dict]) -> None:
    """Persists confirmed answers to a resume's clarifying_questions into the
    master profile's gap_interview_answers - the same store/shape
    profile/interview.py's save_answer() already writes to, so a fact
    confirmed here (e.g. "SK Life Science IT team size/budget") becomes
    available to every future job's drafting, not just this one.
    answered_questions is the subset of clarifying_questions the candidate
    actually typed a real answer for (blank ones already filtered out by the
    caller), each a {"skill":, "type":, "answer":, "question":} dict -
    "question" is optional (older callers may omit it) and is stored so the
    Profile Gaps tab's "previously answered" view can show what was
    actually asked, not just the short skill label. "type" ==
    "disqualifier_check" is saved with is_disqualifier=True so
    SCORE_SYSTEM_PROMPT applies it to every future job, not just this one;
    anything else (including missing/old-shape entries) saves as an ordinary
    skill-gap fact. Never called with an invented answer - the Results tab
    UI only calls this with what the candidate actually typed."""
    from datetime import datetime, timezone

    from profile.interview import save_answer

    role_context = f"{job.get('title', 'Unknown role')} at {job.get('organization', 'Unknown organization')}"
    # UTC, not local time (real bug found live 2026-08-08, General): this
    # gets compared against applications.py's documents_drafted_at, which
    # is stamped in UTC - a local-time date here disagrees with it for
    # part of every day (e.g. any evening in a timezone behind UTC, once
    # UTC has already rolled to the next calendar date), making
    # check_regenerate_impact()'s "has new info since last draft"
    # comparison silently wrong in either direction depending on which
    # side of midnight either clock happens to be on.
    today = datetime.now(timezone.utc).date().isoformat()
    for q in answered_questions:
        answer = (q.get("answer") or "").strip()
        if not answer:
            continue
        save_answer(
            skill=q["skill"], role_context=role_context, answer=answer, date_captured=today,
            question=q.get("question", ""), is_disqualifier=(q.get("type") == "disqualifier_check"),
        )
