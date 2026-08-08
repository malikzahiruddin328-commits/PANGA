"""Direct-API extraction of individual job listings from job-alert digest
emails (LinkedIn "Jobs you may be interested in", Lensa, etc.) -
scripts/job_alert_scan.py's reasoning step, replacing the manual "read the
digest, call add_manual_job() by hand" process CLAUDE.md used to describe
(see that file's "Processing job-alert emails into job records" section,
updated once this shipped). Same llm_client.call_structured direct-API
pattern as tailoring/cta_reasoning.py.

A single digest email commonly bundles several distinct postings (unlike
a CTA email, which is always about exactly one application) -
extract_listings() returns a list, possibly empty (e.g. a "your search
had no new results" digest, or an account-notification email that
matched the sender allowlist but isn't actually a listings digest).

max_tokens escalation (2026-08-08): real gap found via a manual backlog
catch-up run against actual LinkedIn/Lensa digests - the original
hardcoded max_tokens=1500 truncated on any digest bundling more than
roughly 6-8 listings, and most real digests that day had 15-30+ (2 of 16
real threads failed outright before a manual workaround retried with
escalating max_tokens up to 32000 and got them through). A single fixed
ceiling can't cover both a typical single-listing alert (where 32000
would just be wasted budget on every call) and a large bundled digest
(where even 8000-16000 wasn't always enough) - extract_listings() now
retries across _MAX_TOKENS_TIERS on a genuinely truncated response
(llm_client.LLMResponseTruncated) rather than committing to one number,
using the same escalation the manual workaround already proved works
against real digest sizes."""

from llm_client import LLMResponseTruncated, call_structured, get_client

# Starts at 8000 (comfortably covers the large majority of real digests -
# a single-listing alert needs a small fraction of this) rather than the
# old 1500, and escalates only if that genuinely wasn't enough - 16000
# then 32000, the largest max_tokens a real digest was observed to need.
# Not a per-input formula (e.g. scaling off body length): the real digests
# that needed the most tokens weren't reliably the longest bodies, so a
# formula risked either under-provisioning a large one or over-paying for
# every small one - escalating only on an actual truncation is the
# proven-correct behavior instead of a guessed heuristic.
_MAX_TOKENS_TIERS = [8000, 16000, 32000]

_EXTRACT_SYSTEM_PROMPT = """You are extracting individual job listings from a job-alert digest email, for Zahir Uddin's job-search tool "Panga". The email may bundle several distinct postings ("5 new jobs matching..."), or just one.

For each distinct posting found, extract:
- title: the job title, as written.
- organization: the hiring company/employer name. Leave "" if genuinely not stated in the text - do not guess or infer one from unrelated context (e.g. a staffing agency's own name is not the employer).
- location: city/state/remote, as written. "" if not stated.
- posting_url: the direct link to view/apply for that specific posting, if the email contains one. "" if not present.
- description: any actual job-description text included in the email body itself (most digests only link out and describe nothing beyond the title). "" if there's no real description text - do not fabricate or summarize from the title alone.

Extract every distinct posting the email contains regardless of how well it seems to fit Zahir's background or industry - that judgment belongs to Panga's separate fit-scoring step, not this extraction step, and skipping a listing here means it never reaches him to evaluate at all.

If the email isn't actually a job-listing digest (an account notification, a "complete your profile" nudge, a newsletter with no actual postings), return an empty list rather than inventing one."""

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "listings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "organization": {"type": "string"},
                    "location": {"type": "string"},
                    "posting_url": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title", "organization", "location", "posting_url", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["listings"],
    "additionalProperties": False,
}


def extract_listings(subject: str, body: str) -> list[dict]:
    """Returns a list of {"title", "organization", "location",
    "posting_url", "description"} dicts, one per distinct posting found -
    possibly empty. Any field the email doesn't actually state comes back
    as "" rather than a guess: job_alert_scan.py's caller relies on this,
    since a listing with a real posting_url but a blank organization/
    description still gets saved - the existing paste-JD-manually UX
    (ui/app.py's render_paste_jd_prompt_before_drafting, which already
    triggers off an empty job["description"]) picks up from there, same
    as it does for any other thin job record.

    Escalates max_tokens across _MAX_TOKENS_TIERS if the response comes
    back genuinely truncated (LLMResponseTruncated) - a large bundled
    digest can legitimately need more budget than a typical one (see
    module docstring). Raises LLMCallFailed if even the largest tier still
    truncates, or on any other API failure (refusal, invalid JSON, a
    non-truncation error) - those aren't retried here, since a bigger
    token budget wouldn't fix them; the caller is responsible for catching
    that per-message, same as tailoring.cta_reasoning.classify_thread."""
    client = get_client()
    content = f"Subject: {subject}\n\nBody:\n{body}"
    for max_tokens in _MAX_TOKENS_TIERS:
        try:
            data = call_structured(
                client,
                system=_EXTRACT_SYSTEM_PROMPT,
                user_content=content,
                schema=_EXTRACT_SCHEMA,
                max_tokens=max_tokens,
                effort="medium",
                thinking=False,
                refusal_message="Claude declined to extract listings from this email.",
            )
        except LLMResponseTruncated:
            if max_tokens == _MAX_TOKENS_TIERS[-1]:
                raise
            continue
        return data["listings"]
