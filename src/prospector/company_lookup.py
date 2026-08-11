"""One-time web lookup of a target account's real company website (Zahir's
explicit ask 2026-07-31: a URL column next to the company name on the
Prospector tab). Same deliberate direct-API pattern as tailoring/drafting.py's
_lookup_company_address() - a real web search via the Claude API's
server-side tool, never a guessed URL. Costs a small real API call per
lookup, so callers should cache the result (target_accounts.set_website())
and only search once per company, not on every page render.
"""

import re

from llm_client import DEFAULT_MODEL, call_with_web_search, get_client as _client

# Real bug found live 2026-07-31: despite the system prompt saying "ONLY
# the URL, no commentary", the model sometimes prepends a short lead-in
# sentence anyway (e.g. "I'll search for this company's official website.
# https://brainstorm-cell.com") - the old exact-match validation (reject
# if the text contains any space) threw away the real URL along with the
# unwanted preamble. Only 2 of 36 real lookups survived that check in one
# batch. Extracting the URL by pattern instead of requiring the whole
# response to BE the URL fixes this without loosening what counts as a
# genuine find - still never invents a URL, just tolerates a stray
# sentence around a real one.
_URL_RE = re.compile(r"https?://[^\s\"')]+")


def lookup_company_website(company_name: str) -> tuple[str | None, float]:
    """Looks up a company's real official website via web search. Returns
    (url_or_none, cost_usd) - None on no confident match (never guessed from
    the name alone - e.g. 'Acme Bio' is not assumed to be acme.com). Cost is
    computed from the real response usage via api_cost.estimate_response_cost
    (input/output tokens + searches actually run), not an estimate - 0.0 if
    the call itself failed before any usage was billed."""
    client = _client()
    text, cost = call_with_web_search(
        client,
        system=(
            "Look up one company's real, official website homepage URL. "
            "Search the web and confirm it - never guess from the "
            "company name alone. Start with a plain query like "
            "'<company name> official website' or '<company name> "
            "homepage' - for a company that recently filed for an IPO "
            "or was just spun out, general search results are often "
            "dominated by SEC/finance/news aggregator pages (Bloomberg, "
            "stockanalysis.com, EDGAR, TradingView); don't stop at "
            "those, keep searching for the company's own site "
            "specifically before giving up. Reply with ONLY the URL "
            "(e.g. https://www.example.com), nothing else - no "
            "commentary. If you cannot find a confident, verifiable "
            "official website after genuinely trying, reply with "
            "exactly: NOT_FOUND"
        ),
        user_content=f"Company: {company_name}",
        max_tokens=200,
        max_uses=5,
        model=DEFAULT_MODEL,
        purpose="company_website_lookup",
    )
    match = _URL_RE.search(text)
    if not match:
        return None, cost
    return match.group(0).rstrip(".,;:)"), cost
