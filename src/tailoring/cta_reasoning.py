"""Direct-API replacement for the reasoning steps panga-gmail-cta-scan and
panga-cta-fulfillment did live in a Claude Code conversation (native-
packaging branch, 2026-07-31) - classifying an inbox thread, matching an
application-confirmation email to a specific job record, and composing a
short reply. Ported from the exact wording of those two SKILL.md prompts
(see docs/email-monitoring-task.md) so behavior stays comparable, not a
fresh redesign. Uses llm_client the same way every other direct-API module
in Panga does.
"""

import json

from llm_client import call_structured, get_client

_TARGETING_CONTEXT = (
    "Zahir Uddin targets CIO / Head of IT / SVP / VP / Director roles, "
    "primarily life sciences/pharma but open cross-industry (finance, "
    "media, energy, insurance - his background spans AbbVie, Eisai, TD "
    "Bank, Great American Financial, Univision, EMC/BP/Ethicon-J&J/The "
    "Hartford). He's actively job-searching via USAJOBS.gov and various "
    "pharma-specific boards and recruitment firms (Planet Pharma, "
    "BioSpace, and others). Recruiter/ATS domains to watch for: "
    "greenhouse.io, lever.co, myworkday.com, icims.com, smartrecruiters.com, "
    "taleo.net, successfactors.com, hirevue.com, calendly.com (interview "
    "scheduling), plus direct recruiter/company domains."
)

_CLASSIFY_SYSTEM_PROMPT = f"""You are classifying one Gmail thread from Zahir Uddin's personal inbox (not a job-only mailbox) for his job-search tool "Panga". Be precise, not over-inclusive - most of his inbox is unrelated personal mail.

{_TARGETING_CONTEXT}

Classify the thread into exactly one bucket:
- "not_related": personal/unrelated mail, not about his job search at all.
- "passive": job-search-related but no action needed (job-alert digests, newsletters).
- "call_to_action": an interview invite/scheduling request, an assessment or take-home task request, a job offer, a rejection, or a recruiter asking a direct question / requesting a reply or call. If this bucket, also set cta_category to the single closest fit: "interview_request", "assessment_request", "offer", "rejection", or "recruiter_question".
- "application_confirmation": an application-received/confirmation email (e.g. "thank you for applying to X", "we received your application").

Set cta_category to "" (empty string) unless bucket is "call_to_action".

IMPORTANT - don't classify by sender domain or subject wording alone, read the snippet:
- Bulk job-alert digests (senders like jobalert@lensa.com, notifications@monster.com, or a ZipRecruiter/Indeed "jobs like X" or "N more jobs" email) are genuinely "passive" - no individual reviewed his situation.
- But a single-position email FROM AN ATS PLATFORM (greenhouse-mail.io, newtonsoftware.com, icims.com, myworkday.com, lever.co, smartrecruiters.com, taleo.net, successfactors.com, etc.) about ONE specific role is NOT automatically passive just because it's automated and reads politely - these are exactly where real rejections and interview invites live, and they often use soft, generic-sounding phrasing ("we've had a chance to review your resume and compare it against other candidates..." IS a rejection, not a status update - read for the actual outcome, don't be fooled by polite corporate phrasing into calling it passive). If the snippet describes a specific decision or ask about a specific application, that's "call_to_action" (rejection/interview_request/etc.), even from an automated ATS sender.
- This applies EQUALLY to a single-position email from an individual person's own address (a staffing-firm recruiter, an in-house recruiter, a hiring manager) - not only known ATS platform domains. A message that personally addresses him about ONE specific role and asks a direct question ("I'm hiring for X, would you be interested/available?", "let's schedule a call", "does this fit?") is "call_to_action" / recruiter_question regardless of whether the sender's domain is a recognized ATS platform, a staffing firm, or a company's own mail server - the test is "is this addressed to him personally about one specific thing and does it ask something of him," not "is the domain on a known list."
- Gmail's snippet is frequently truncated MID-SENTENCE, sometimes right before the actual outcome is stated - e.g. "...we've had a chance to review your resume and compare it against other candidates" can cut off the instant before the word "Unfortunately" or "Congratulations" that would tell you which way it went. Do not treat a snippet like this as "passive" just because the visible text sounds neutral or in-progress - a snippet that describes a resume/application being reviewed or compared, WITHOUT yet stating a clear outcome, is exactly the "unhelpful" case below, not evidence of "no action needed."
- If the snippet is empty, cuts off before a decision/ask is actually stated, or the sender is a single-position ATS/recruiter/individual address (not a bulk digest domain) and you're not fully sure, set confident=false rather than guessing "passive" by default - it's worth the full-thread-body pass rather than risking a missed rejection, interview invite, or direct recruiter outreach.

Set confident to false whenever the subject/sender/snippet alone genuinely isn't enough to classify correctly and you'd want the full thread body before deciding - including the ATS-single-position case and the truncated-snippet case above, not just a general hedge. A false positive here just costs one extra full-body API call; a false negative here means a real rejection or recruiter outreach silently never reaches Zahir - bias toward the extra check."""

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "bucket": {
            "type": "string",
            "enum": ["not_related", "passive", "call_to_action", "application_confirmation"],
        },
        "cta_category": {
            "type": "string",
            "enum": ["", "interview_request", "assessment_request", "offer", "rejection", "recruiter_question"],
        },
        "confident": {"type": "boolean"},
    },
    "required": ["bucket", "cta_category", "confident"],
    "additionalProperties": False,
}


def classify_thread(thread_summary: dict, full_body: str | None = None) -> dict:
    """thread_summary needs subject/sender/date/snippet (gmail_client.
    search_threads()'s shape). Pass full_body (gmail_client.get_thread()'s
    decoded text, joined across messages) on a second call if the first
    call came back with confident=False. Returns {"bucket": str,
    "cta_category": str, "confident": bool}."""
    client = get_client()
    content = (
        f"Subject: {thread_summary.get('subject', '')}\n"
        f"From: {thread_summary.get('sender', '')}\n"
        f"Date: {thread_summary.get('date', '')}\n"
        f"Snippet: {thread_summary.get('snippet', '')}"
    )
    if full_body:
        content += f"\n\nFull thread body:\n{full_body}"
    return call_structured(
        client,
        system=_CLASSIFY_SYSTEM_PROMPT,
        user_content=content,
        schema=_CLASSIFY_SCHEMA,
        max_tokens=500,
        effort="medium",
        thinking=False,
        refusal_message="Claude declined to classify this email. Treating as not_related for safety.",
        purpose="cta_classify_thread",
    )


_ORG_MISMATCH_WARNING = (
    "Organization name is usually a far more reliable disambiguator than "
    "title wording - two different real job postings frequently share "
    "similar or overlapping title text (e.g. both containing the word "
    "\"Digital\"), but rarely share an employer name. Weigh the "
    "organization the email is actually from (its sender domain, "
    "letterhead, or explicit company name in the body) more heavily than "
    "title similarity - a candidate whose organization clearly doesn't "
    "match the email's real sender/company is a bad match even if its "
    "title looks similar, and a candidate whose organization DOES match "
    "is a strong signal even if the title wording differs slightly. Real "
    "incident (2026-08-09): a rejection email that opened by naming its "
    "own organization was matched to a completely different candidate at "
    "an unrelated company, purely on loose title-word overlap - don't "
    "repeat that mistake."
)

_MATCH_SYSTEM_PROMPT = f"""You are matching an application-confirmation email to a specific job record from Zahir Uddin's job-search tool "Panga", so his application status can be updated automatically. Only report a match if you are genuinely confident - if the email doesn't give enough detail to distinguish between multiple candidate jobs (e.g. two identically-titled postings at different times), do not guess; report matched=false instead. A wrong match is worse than no match.

{_ORG_MISMATCH_WARNING}"""

_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matched": {"type": "boolean"},
        "source": {"type": "string", "description": "The matched job's source field. Empty string if matched=false."},
        "job_id": {"type": "string", "description": "The matched job's job_id field. Empty string if matched=false."},
        "reason": {"type": "string", "description": "One sentence: what the email said, or why no confident match exists."},
    },
    "required": ["matched", "source", "job_id", "reason"],
    "additionalProperties": False,
}


_MIN_ORG_HINT_LENGTH = 4  # a name shorter than this (e.g. "BD") is too
# generic to safely substring-match against arbitrary email text without
# risking a false positive - short/acronym organization names are left to
# the LLM's own judgment, same as before this fix existed.


def _organization_hint(full_body: str, candidate_jobs: list[dict]) -> str:
    """Deterministic pre-check, appended to the match call's content when
    it fires: if exactly one candidate's organization name is explicitly
    stated in the email body, surface that as an explicit hint rather
    than leaving the LLM to notice it unassisted. Real gap found
    2026-08-09 (Zahir spotted a wrong live match, traced by General): a
    rejection email that opened by literally naming its own organization
    ("UAB Medicine emailed... not selected for the Chief Digital and
    Information Officer") still got matched to a completely unrelated
    candidate at a different company ("BD"), purely on loose title-word
    overlap ("Digital" in both titles) - both candidates had real,
    populated organization fields already (the earlier "candidates have
    no title/org" join gap, fixed 2026-08-07, was NOT the cause here;
    this is a genuine matching miss against otherwise-good data, a
    different failure mode). Returns "" (no hint) when zero or more than
    one candidate's organization appears - an ambiguous or absent signal
    is left entirely to the LLM's judgment, same as before this fix."""
    lowered_body = full_body.lower()
    matches = [
        job for job in candidate_jobs
        if (org := (job.get("organization") or "").strip())
        and len(org) >= _MIN_ORG_HINT_LENGTH
        and org.lower() in lowered_body
    ]
    if len(matches) != 1:
        return ""
    job = matches[0]
    return (
        f"\n\nDETERMINISTIC HINT: the organization name '{job['organization']}' "
        f"appears verbatim in the email body, and exactly one candidate "
        f"(source={job['source']}, job_id={job['job_id']}) has that "
        "organization. Per the organization-vs-title guidance above, give "
        "this candidate strong weight unless the rest of the email clearly "
        "contradicts it."
    )


def match_application_confirmation(thread_summary: dict, full_body: str, candidate_jobs: list[dict]) -> dict:
    """candidate_jobs is the list of applications currently "under review"
    (tailoring.applications.load_applications() filtered by the caller).
    Returns {"matched": bool, "source": str, "job_id": str, "reason": str}."""
    client = get_client()
    content = (
        f"CONFIRMATION EMAIL:\nSubject: {thread_summary.get('subject', '')}\n"
        f"From: {thread_summary.get('sender', '')}\nBody:\n{full_body}\n\n"
        "CANDIDATE JOBS CURRENTLY 'UNDER REVIEW':\n" + json.dumps(candidate_jobs, indent=2, default=str)
        + _organization_hint(full_body, candidate_jobs)
    )
    return call_structured(
        client,
        system=_MATCH_SYSTEM_PROMPT,
        user_content=content,
        schema=_MATCH_SCHEMA,
        max_tokens=500,
        effort="medium",
        thinking=False,
        refusal_message="Claude declined to match this email. Treating as no match for safety.",
        purpose="cta_match_application_confirmation",
    )


_CTA_MATCH_SYSTEM_PROMPT = f"""You are matching a job-search email (a rejection, interview request, or offer) to a specific job record from Zahir Uddin's job-search tool "Panga", so his application status can be updated automatically. Only report a match if you are genuinely confident - if the email doesn't give enough detail to distinguish between multiple candidate jobs (e.g. two identically-titled postings at different companies, or the email is generic/ambiguous about which role it's about), do not guess; report matched=false instead. A wrong match is worse than no match - it would silently mark the wrong application as rejected/interviewing/offered.

{_ORG_MISMATCH_WARNING}"""

# Same shape as _MATCH_SCHEMA above, kept as its own object rather than
# shared so each prompt's schema description text stays accurate to what
# it's actually describing (a "confirmation" email there, any of
# rejection/interview_request/offer here).
_CTA_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matched": {"type": "boolean"},
        "source": {"type": "string", "description": "The matched job's source field. Empty string if matched=false."},
        "job_id": {"type": "string", "description": "The matched job's job_id field. Empty string if matched=false."},
        "reason": {"type": "string", "description": "One sentence: what the email said, or why no confident match exists."},
    },
    "required": ["matched", "source", "job_id", "reason"],
    "additionalProperties": False,
}


def match_cta_application(category: str, thread_summary: dict, full_body: str, candidate_jobs: list[dict]) -> dict:
    """Matches a rejection/interview_request/offer email to a specific
    application - same "confident match or explicitly no match" pattern
    as match_application_confirmation above, extended to the CTA
    categories that actually represent a status transition (see
    scripts/gmail_cta_scan.py's _CTA_STATUS_BY_CATEGORY for why
    assessment_request/recruiter_question are deliberately excluded - a
    question or an assessment ask isn't a status change, and there's no
    corresponding value in applications.py's status lifecycle for either).
    `candidate_jobs` is every application not already in a terminal status
    (rejected/not interested/save for later/closed by employer) - the
    caller is responsible for that filtering, this function only matches
    within whatever list it's handed. Returns {"matched": bool, "source":
    str, "job_id": str, "reason": str}."""
    client = get_client()
    content = (
        f"CATEGORY: {category}\n"
        f"Subject: {thread_summary.get('subject', '')}\n"
        f"From: {thread_summary.get('sender', '')}\nBody:\n{full_body}\n\n"
        "CANDIDATE APPLICATIONS (not yet in a final/closed status):\n" + json.dumps(candidate_jobs, indent=2, default=str)
        + _organization_hint(full_body, candidate_jobs)
    )
    return call_structured(
        client,
        system=_CTA_MATCH_SYSTEM_PROMPT,
        user_content=content,
        schema=_CTA_MATCH_SCHEMA,
        max_tokens=500,
        effort="medium",
        thinking=False,
        refusal_message="Claude declined to match this email. Treating as no match for safety.",
        purpose="cta_match_application",
    )


_DRAFT_REPLY_SYSTEM_PROMPT = f"""You are composing a short, professional email reply on Zahir Uddin's behalf, for his job-search tool "Panga". This becomes a Gmail DRAFT only - it is never sent automatically, Zahir reviews and sends it himself, so err toward a reasonable draft rather than declining.

{_TARGETING_CONTEXT}

Write 2-4 sentences tailored to the category and the actual subject/snippet content:
- offer: express genuine interest/thanks, ask about next steps (start date, comp details if not covered, etc).
- interview_request: confirm enthusiasm. If "Available times" are given below, propose exactly those (don't invent others, and don't offer every one of your open hours - a shortlist reads as a normal, in-demand schedule, not as "I'm free whenever," which is the whole point of only being given a curated few). If no available times are given, ask them to propose times instead (don't invent a specific date/time you don't have).
- assessment_request: acknowledge receipt, confirm you'll complete it, ask about the deadline if unclear.
- recruiter_question: answer helpfully based on Zahir's background above if the question is answerable from that; otherwise keep it brief and ask a clarifying question back.
- rejection: brief, gracious thank-you, express interest in being considered for future roles.

Sign off as Zahir. Plain text only, no markdown, no subject line (the caller adds that separately)."""

_DRAFT_REPLY_SCHEMA = {
    "type": "object",
    "properties": {"reply_body": {"type": "string", "description": "2-4 sentence plain-text reply body, signed as Zahir."}},
    "required": ["reply_body"],
    "additionalProperties": False,
}


def draft_cta_reply(category: str, subject: str, snippet: str, available_slots: list[str] | None = None) -> str:
    """category is one of "rejection", "interview_request",
    "assessment_request", "offer", "recruiter_question" (cta_emails.py's
    stored category field). `available_slots` is an optional list of
    human-readable time strings (e.g. "Tuesday, Aug 5, 2:00-2:30 PM") - the
    caller is responsible for producing these (see
    google_calendar_client.curate_believable_slots() for the Google
    Calendar path) and for this being real availability, not invented;
    this function just decides whether to use them. Only meaningful for
    category="interview_request" - ignored for every other category, same
    as the caller passing None. Returns the composed reply body text."""
    client = get_client()
    content = f"Category: {category}\nSubject: {subject}\nSnippet: {snippet}"
    if available_slots:
        content += "\nAvailable times:\n" + "\n".join(f"- {slot}" for slot in available_slots)
    data = call_structured(
        client,
        system=_DRAFT_REPLY_SYSTEM_PROMPT,
        user_content=content,
        schema=_DRAFT_REPLY_SCHEMA,
        max_tokens=500,
        effort="medium",
        thinking=False,
        refusal_message="Claude declined to draft a reply.",
        purpose="cta_draft_reply",
    )
    return data["reply_body"]
