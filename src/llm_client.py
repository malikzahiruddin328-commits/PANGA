"""Shared direct-Anthropic-API plumbing (native-packaging branch,
2026-07-31). Every direct-API module in Panga - tailoring/drafting.py,
prospector/prospector_score.py, prospector/company_lookup.py, and the
standalone-script reasoning this branch is adding (fit scoring, CTA
classification/drafting, interview prep, LinkedIn enhance, rejection
diagnosis, learn engine) - independently reimplemented client setup, the
streamed-structured-output call pattern, and the same API-error handling.
This module is the one place that plumbing lives now. The original modules
keep their old public names (DraftingNotConfigured, DraftingFailed,
DEFAULT_MODEL, _client) as re-exports so no existing import elsewhere in
the app needs to change.
"""

import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_MODEL = "claude-opus-5"


class LLMNotConfigured(Exception):
    pass


class LLMCallFailed(Exception):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def get_client() -> "anthropic.Anthropic":
    if not is_configured():
        raise LLMNotConfigured(
            "ANTHROPIC_API_KEY must be set in .env for direct-API calls. "
            "Get a key at console.anthropic.com, then add it to the .env "
            "file in the Panga folder (copy the same line USAJOBS_API_KEY "
            "uses) and restart the app."
        )
    return anthropic.Anthropic()


def call_structured(
    client: "anthropic.Anthropic",
    system,
    user_content,
    schema: dict,
    max_tokens: int,
    model: str | None = None,
    effort: str = "high",
    thinking: bool = True,
    on_progress=None,
    refusal_message: str = "Claude declined to respond. Try again.",
) -> dict:
    """One streamed call constrained to `schema` via structured output.
    Reports "thinking..."/"writing... (N characters so far)" through
    on_progress(substatus) as the response streams - same real-progress
    mechanism every direct-API module in Panga already implemented
    separately (Zahir's ask 2026-07-31: no spinner should be opaque when
    the underlying call can report real progress instead). Callers that
    need per-item progress framing (e.g. "drafting doc 2 of 5") wrap
    on_progress themselves before passing it in - this helper only knows
    about a single current-call substatus string.

    Returns the parsed JSON dict. Raises LLMNotConfigured (via the caller's
    own get_client() call, before this function is even reached) and
    LLMCallFailed on API error, refusal, truncation, or invalid JSON."""
    kwargs = dict(
        model=model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL,
        max_tokens=max_tokens,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    if thinking:
        kwargs["thinking"] = {"type": "adaptive"}
    try:
        with client.messages.stream(**kwargs) as stream:
            char_count = 0
            last_reported = 0
            for event in stream:
                if event.type == "content_block_start" and event.content_block.type == "thinking":
                    if on_progress:
                        on_progress("thinking...")
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    char_count += len(event.delta.text)
                    if on_progress and char_count - last_reported >= 150:
                        on_progress(f"writing... ({char_count:,} characters so far)")
                        last_reported = char_count
            response = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        raise LLMCallFailed(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMCallFailed("Couldn't reach the Claude API - check your internet connection.") from exc

    if response.stop_reason == "refusal":
        raise LLMCallFailed(refusal_message)
    if response.stop_reason == "max_tokens":
        raise LLMCallFailed("The response was cut off before finishing. Try again.")

    text_block = next((b.text for b in response.content if b.type == "text"), None)
    if not text_block:
        raise LLMCallFailed("Claude returned no result.")
    try:
        return json.loads(text_block)
    except json.JSONDecodeError as exc:
        raise LLMCallFailed("Claude's response wasn't valid - try again.") from exc


def call_with_web_search(
    client: "anthropic.Anthropic",
    system: str,
    user_content: str,
    max_tokens: int,
    max_uses: int = 5,
    model: str | None = None,
) -> tuple[str, float]:
    """One-shot (non-streamed) call with the server-side web_search tool -
    the pattern tailoring/drafting.py's _lookup_company_address() and
    prospector/company_lookup.py's lookup_company_website() both
    implemented separately. Returns (response_text, cost_usd), computed
    from the real response usage via api_cost.estimate_response_cost - cost
    is 0.0 if the call failed before any usage was billed. Never raises on
    API error (both original callers treated a failed lookup as "no
    result found", not a hard error) - callers check for an empty string."""
    from api_cost import estimate_response_cost

    call_model = model or DEFAULT_MODEL
    try:
        response = client.messages.create(
            model=call_model,
            max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
    except (anthropic.APIStatusError, anthropic.APIConnectionError):
        return "", 0.0

    cost = estimate_response_cost(response, call_model)
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text, cost
