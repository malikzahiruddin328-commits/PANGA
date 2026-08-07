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
matched the sender allowlist but isn't actually a listings digest)."""

from llm_client import call_structured, get_client

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
    as it does for any other thin job record. Raises LLMCallFailed on API
    error/refusal/truncation - the caller is responsible for catching
    that per-message, same as tailoring.cta_reasoning.classify_thread."""
    client = get_client()
    content = f"Subject: {subject}\n\nBody:\n{body}"
    data = call_structured(
        client,
        system=_EXTRACT_SYSTEM_PROMPT,
        user_content=content,
        schema=_EXTRACT_SCHEMA,
        max_tokens=1500,
        effort="medium",
        thinking=False,
        refusal_message="Claude declined to extract listings from this email.",
    )
    return data["listings"]
