"""One-time web lookup of a target account's real company website (Zahir's
explicit ask 2026-07-31: a URL column next to the company name on the
Prospector tab). Same deliberate direct-API pattern as tailoring/drafting.py's
_lookup_company_address() - a real web search via the Claude API's
server-side tool, never a guessed URL. Costs a small real API call per
lookup, so callers should cache the result (target_accounts.set_website())
and only search once per company, not on every page render.
"""

import anthropic

from api_cost import estimate_response_cost
from tailoring.drafting import DEFAULT_MODEL, _client


def lookup_company_website(company_name: str) -> tuple[str | None, float]:
    """Looks up a company's real official website via web search. Returns
    (url_or_none, cost_usd) - None on no confident match (never guessed from
    the name alone - e.g. 'Acme Bio' is not assumed to be acme.com). Cost is
    computed from the real response usage via api_cost.estimate_response_cost
    (input/output tokens + searches actually run), not an estimate - 0.0 if
    the call itself failed before any usage was billed."""
    client = _client()
    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=200,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            system=(
                "Look up one company's real, official website homepage URL. "
                "Search the web and confirm it - never guess from the "
                "company name alone. Reply with ONLY the URL "
                "(e.g. https://www.example.com), nothing else - no "
                "commentary. If you cannot find a confident, verifiable "
                "official website, reply with exactly: NOT_FOUND"
            ),
            messages=[{"role": "user", "content": f"Company: {company_name}"}],
        )
    except (anthropic.APIStatusError, anthropic.APIConnectionError):
        return None, 0.0

    cost = estimate_response_cost(response, DEFAULT_MODEL)

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text or text == "NOT_FOUND" or len(text) > 300 or " " in text:
        return None, cost
    return text, cost
