"""Direct-API replacement for the Prospector cold-outreach composition step
panga-cta-fulfillment's STEP 2C did live in a Claude Code conversation (see
docs/manual-sync-button-scope.md) - reused by both the 2x/day scheduled
fulfillment run and the dashboard's synchronous "Send and receive" button
(src/fulfillment.py calls this either way). Ported from that SKILL.md
step's exact wording so behavior stays comparable, not a fresh redesign.
Uses llm_client the same way every other direct-API module in Panga does.

Reuses tailoring.cta_reasoning's _TARGETING_CONTEXT rather than
duplicating the same descriptive paragraph about Zahir's background - it's
the identical targeting profile whether the email is a reply or a cold
intro.
"""

from llm_client import call_structured, get_client
from tailoring.cta_reasoning import _TARGETING_CONTEXT

_DRAFT_OUTREACH_SYSTEM_PROMPT = f"""You are composing a short, professional COLD outreach email on Zahir Uddin's behalf, for his job-search tool "Panga". This becomes a Gmail DRAFT only - it is never sent automatically, Zahir reviews and sends it himself, so err toward a reasonable draft rather than declining.

{_TARGETING_CONTEXT}

There is no prior thread - this introduces Zahir to someone he hasn't corresponded with yet. Write 2-4 sentences: introduce Zahir briefly, reference why he's reaching out (the context given below - a target account he's interested in, or a specific job he's linked this contact to), and ask for a brief conversation.

Sign off as Zahir. Plain text only, no markdown, no subject line (the caller adds that separately)."""

_DRAFT_OUTREACH_SCHEMA = {
    "type": "object",
    "properties": {"email_body": {"type": "string", "description": "2-4 sentence plain-text cold outreach email, signed as Zahir."}},
    "required": ["email_body"],
    "additionalProperties": False,
}


def draft_outreach_email(contact_name: str, contact_title: str | None, context: str) -> str:
    """`context` is a short plain-language description of why Zahir is
    reaching out - the caller (src/fulfillment.py) resolves this from
    either the outreach record's target_account_name+notes or its linked
    job's title/organization, this function stays uncoupled from those
    data layers (same "reasoning module gets plain strings, doesn't own
    lookups" split cta_reasoning.py already uses). Returns the composed
    email body text."""
    client = get_client()
    content = f"Contact: {contact_name}"
    if contact_title:
        content += f" ({contact_title})"
    content += f"\nContext: {context}"
    data = call_structured(
        client,
        system=_DRAFT_OUTREACH_SYSTEM_PROMPT,
        user_content=content,
        schema=_DRAFT_OUTREACH_SCHEMA,
        max_tokens=500,
        effort="medium",
        thinking=False,
        refusal_message="Claude declined to draft an outreach email.",
        purpose="outreach_draft_email",
    )
    return data["email_body"]
