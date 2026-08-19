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


def run_claude_cli(prompt: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS, on_start=None) -> str:
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

    on_start(pid: int), if given, fires with the REAL OS process ID of the
    `claude` subprocess this exact call just launched, as soon as it's
    known (right after the process starts, before this function blocks
    waiting for it to finish) - added 2026-08-17 so a caller can persist
    that PID (and a start timestamp) onto the application record it's
    working on, closing the real gap Zahir hit live twice today:
    subscription_qa_status only ever said a string like "drafting", with
    no way to tell WHICH of several concurrently-running `claude` OS
    processes (confirmed live: `Get-Process claude` returned 16
    unlabeled ones) belongs to which job, or whether that specific process
    is still alive vs. genuinely hung. Uses subprocess.Popen (not
    subprocess.run) specifically so the real PID is available before the
    call blocks - subprocess.run only returns after the process has
    already exited, too late to ever record its PID for a still-running
    call. Never called if the process fails to start at all (the
    FileNotFoundError case below) - there is no real PID in that case.

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
        # subprocess.Popen, not subprocess.run - see on_start's own
        # docstring note above for why: run() blocks until the process has
        # already exited before handing back anything, so there's no point
        # in its lifetime a caller could ever observe a still-running PID.
        proc = subprocess.Popen(
            ["claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
            text=True, encoding="utf-8",
        )
    except FileNotFoundError as exc:
        raise ReasonerUnavailable("The 'claude' CLI isn't installed or isn't on PATH on this machine.") from exc

    if on_start:
        on_start(proc.pid)

    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        # communicate()'s own docs are explicit that a caught TimeoutExpired
        # leaves the child process still running (unlike subprocess.run,
        # which kills it for you) - without this, a timed-out call would
        # leave an orphaned `claude` process behind with nothing left
        # tracking it (this function's own caller already marked the job
        # "failed" and moved on), exactly the kind of untracked zombie
        # process this whole feature exists to prevent, not just to detect.
        proc.kill()
        proc.communicate()
        raise RuntimeError(f"Reasoner call timed out after {timeout_seconds}s without returning.") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited with code {proc.returncode}: {(stderr or '').strip()[:500]}")
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude CLI returned non-JSON output: {stdout[:500]}") from exc
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI reported an error: {str(envelope.get('result', ''))[:500]}")
    return envelope.get("result", "")


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def _find_json_match(candidate: str):
    """Real bug found 2026-08-19: the object/array regexes above can each
    spuriously match INSIDE the other's structure - e.g. for a top-level
    array of objects, `_JSON_OBJECT_RE`'s greedy `\\{.*\\}` still finds a
    '{' and a '}' somewhere in the text and happily matches (wrongly)
    across every element from the first '{' to the last '}', so which
    regex to try first can't be a fixed order. Instead this looks at which
    bracket - '{' or '[' - actually appears FIRST in the text (this holds
    even with stray leading prose, e.g. "Here is the array [...] - hope
    that helps!") and tries that structure's regex first, falling back to
    the other one only if the first guess finds nothing."""
    first_brace = candidate.find("{")
    first_bracket = candidate.find("[")
    if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        return _JSON_ARRAY_RE.search(candidate) or _JSON_OBJECT_RE.search(candidate)
    if first_brace != -1:
        return _JSON_OBJECT_RE.search(candidate) or _JSON_ARRAY_RE.search(candidate)
    return None


def parse_json_reply(text: str) -> dict | list:
    """Reasoner prompts ask for ONLY a bare JSON object, but a live
    reasoning reply isn't guaranteed to comply exactly (a ```json fence, a
    stray leading/trailing sentence) - the same "AI output feeding a
    literal downstream check needs a real parser, not a bare json.loads()"
    principle CLAUDE.md already calls out elsewhere in this codebase.

    Also handles a top-level JSON array reply the same way (bare, or
    inside a ```json fence) - real bug found 2026-08-19: some callers
    legitimately want a list back, and this function used to be hardcoded
    to objects only, silently failing (or truncating to an inner-object
    fragment) on a bare array reply."""
    text = (text or "").strip()
    fence = _JSON_FENCE_RE.search(text)
    candidate = fence.group(1) if fence else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = _find_json_match(candidate)
        if not match:
            raise RuntimeError(f"Could not find a JSON object or array in the reasoner's reply: {text[:500]}")
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
