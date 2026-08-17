"""Shared subprocess mechanism for invoking the `claude` CLI in
non-interactive print mode as a subscription-covered ("free" against
Panga's own $10/day API cap) reasoning call from inside Panga's own
runtime.

THE REAL MECHANISM, same one proven out today (2026-08-13) on
feature/resume-reasoner-path's src/tailoring/reasoner_pipeline.py for
resume/cover-letter drafting - see that module's own docstring for the
full explanation of why this, and not the `Agent` tool, is what's
actually invokable from a plain Python process. Short version: the
`claude` CLI run as `claude -p "<prompt>" --output-format json
--permission-mode bypassPermissions` authenticates against whatever this
machine's CLI is already logged into (confirmed via `claude auth status`:
{"authMethod": "claude.ai", "subscriptionType": "max"}), not an
ANTHROPIC_API_KEY - so these calls run against the Max subscription's own
quota, never touching llm_client.py's paid-API spend cap.

Factored out here (2026-08-13, fix/discuss-draft-subscription-questions)
because tailoring.discuss_and_draft.start_discussion() needed the exact
same mechanism reasoner_pipeline.py already built for drafting, to
generate this feature's opening clarifying questions - genuinely the same
building block, not a similar-looking reimplementation. reasoner_
pipeline.py lives on a different, not-yet-merged branch
(feature/resume-reasoner-path) with its own private (underscore-prefixed)
copies of this exact logic (_run_claude_cli/_parse_json_reply/
ReasonerUnavailable) - this module can't import from a sibling branch
that isn't part of this branch's own history, so this is a deliberate,
flagged duplication, not an oversight: whichever of these two branches
merges to master second should be reconciled to import from this shared
module instead of carrying its own private copy (a note for the Release
Manager - see docs/release-manager.md - at merge time, not something
either branch's own session can resolve on its own since neither can see
the other's uncommitted worktree state)."""

import json
import re
import subprocess

# Generous, not a target duration - a live reasoning call can genuinely
# take upward of a minute on a large real profile (the resume-reasoner
# path's own equivalent full-draft calls measured 39-63s on real data).
# This is a ceiling against a hung/wedged subprocess never returning at
# all (CLAUDE.md's standing "any call bound by an external process needs
# a timeout, not an unconditional wait" rule), not an expectation of how
# long a normal call takes.
DEFAULT_TIMEOUT_SECONDS = 300


class ReasonerUnavailable(Exception):
    """Raised when the `claude` CLI itself can't be invoked at all (not
    installed, not on PATH, or not logged in) - distinct from an ordinary
    per-call failure, because it means the reasoner mechanism as a whole
    is unusable right now, not just this one call. Callers should surface
    this clearly rather than treating it as an ordinary per-job error."""


def run_claude_cli(prompt: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Invokes the `claude` CLI in non-interactive print mode as a fresh,
    synchronous subprocess and returns its final text reply. Blocks until
    the subprocess returns a result or times out - never depends on any
    other session being awake or online (the real failure mode this
    mechanism was explicitly built to avoid: a message posted to another
    session's inbox that might sit unprocessed).

    --permission-mode bypassPermissions is required for a genuinely
    non-interactive call (this subprocess has no terminal to answer a
    tool-permission prompt on) - safe here because every prompt built by
    this module's callers only ever asks for a JSON text reply; no
    file/bash tool use is requested, expected, or needed.

    The prompt is piped via stdin, NOT passed as a `-p <prompt>` argv
    element - a real bug found live-verifying this module (2026-08-13):
    Windows' CreateProcess has a hard command-line-length ceiling
    (~32,767 characters), and a prompt embedding a real full master
    profile (documented elsewhere in this codebase as ~98,000 characters
    on real data) blows past that immediately, failing with `WinError
    206: The filename or extension is too long` before the subprocess
    even starts - not a timeout or an API-side failure, a Windows argv
    limit. `claude -p` (with no prompt argument) reads the prompt from
    stdin instead, confirmed working live against a real prompt of this
    size - this sidesteps the OS argv limit entirely since stdin has no
    such ceiling. feature/resume-reasoner-path's own reasoner_
    pipeline.py has the same latent bug (its _run_claude_cli() still
    passes the prompt as an argv element) - flagged for Release Manager
    to reconcile at merge time (see this module's own top docstring)."""
    try:
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions"],
            # Real bug found live 2026-08-17 (feature/jd-keyword-taxonomy-gaps,
            # Phase 1 verification against real job posting text): plain
            # text=True lets subprocess pick stdin's encoding from
            # locale.getpreferredencoding() - on this Windows machine that's
            # cp1252, not UTF-8. A real scraped JD containing an ordinary
            # Unicode character outside cp1252 (e.g. "○" as a bullet point,
            # confirmed live on a real Insmed posting) crashed the stdin
            # writer thread with UnicodeEncodeError before the subprocess
            # ever got a chance to run - not a `claude` CLI failure at all,
            # a Python-side encode failure one layer before it. encoding=
            # "utf-8" pins both the stdin write and stdout/stderr decode to
            # UTF-8 regardless of the console's codepage, matching every
            # other place in this codebase that already guards against a
            # Windows console codepage choking on real Unicode job data
            # (see scripts/run_search.py's sys.stdout.reconfigure()). This
            # bug pre-dates this branch - every existing caller of
            # run_claude_cli() (subscription_resume_qa.py,
            # discuss_and_draft.py) was equally exposed to it on any real
            # posting with non-Latin1 text; fixed here rather than worked
            # around locally since it's the shared mechanism, not a bug in
            # any one caller.
            input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout_seconds, check=False,
        )
    except FileNotFoundError as exc:
        raise ReasonerUnavailable("The 'claude' CLI isn't installed or isn't on PATH on this machine.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Reasoner call timed out after {timeout_seconds}s without returning.") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited with code {proc.returncode}: {(proc.stderr or '').strip()[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude CLI returned non-JSON output: {proc.stdout[:500]}") from exc
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI reported an error: {str(envelope.get('result', ''))[:500]}")
    return envelope.get("result", "")


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_json_reply(text: str) -> dict:
    """Reasoner prompts ask for ONLY a bare JSON object, but a live
    reasoning reply isn't guaranteed to comply exactly (a ```json fence, a
    stray leading/trailing sentence) - the same "AI output feeding a
    literal downstream check needs a real parser, not a bare json.loads()"
    principle CLAUDE.md already calls out elsewhere in this codebase."""
    text = (text or "").strip()
    fence = _JSON_FENCE_RE.search(text)
    candidate = fence.group(1) if fence else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(candidate)
        if not match:
            raise RuntimeError(f"Could not find a JSON object in the reasoner's reply: {text[:500]}")
        # Real bug found 2026-08-17 (feature/basket-consolidated-flow, live-
        # verifying the new basket-wide "Draft & score all" loop): a reply
        # can contain something that LOOKS like a JSON object (matches the
        # regex) but still isn't valid JSON - e.g. a raw/unescaped control
        # character inside a string value the model wrote. That crashed as
        # an uncaught json.JSONDecodeError straight out of this function,
        # even though every caller's own docstring (run_subscription_round,
        # etc.) promises "RuntimeError on a genuine per-call failure...
        # never swallowed" as the ONLY non-ReasonerUnavailable failure
        # shape - an uncaught JSONDecodeError breaks that contract and, for
        # any caller looping over multiple jobs without its own per-item
        # try/except, takes the whole loop down with it (the exact
        # "one item's failure shouldn't stop the rest" pattern CLAUDE.md
        # calls out). Wrap it the same way the first attempt already is.
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Reasoner's reply looked like JSON but failed to parse: {exc}. Raw: {text[:500]}") from exc
