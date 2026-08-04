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
    )


_MATCH_SYSTEM_PROMPT = """You are matching an application-confirmation email to a specific job record from Zahir Uddin's job-search tool "Panga", so his application status can be updated automatically. Only report a match if you are genuinely confident - if the email doesn't give enough detail to distinguish between multiple candidate jobs (e.g. two identically-titled postings at different times), do not guess; report matched=false instead. A wrong match is worse than no match."""

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


def match_application_confirmation(thread_summary: dict, full_body: str, candidate_jobs: list[dict]) -> dict:
    """candidate_jobs is the list of applications currently "under review"
    (tailoring.applications.load_applications() filtered by the caller).
    Returns {"matched": bool, "source": str, "job_id": str, "reason": str}."""
    client = get_client()
    content = (
        f"CONFIRMATION EMAIL:\nSubject: {thread_summary.get('subject', '')}\n"
        f"From: {thread_summary.get('sender', '')}\nBody:\n{full_body}\n\n"
        "CANDIDATE JOBS CURRENTLY 'UNDER REVIEW':\n" + json.dumps(candidate_jobs, indent=2, default=str)
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
    )
    return data["reply_body"]
