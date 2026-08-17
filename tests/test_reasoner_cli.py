"""tailoring.reasoner_cli (2026-08-13, fix/discuss-draft-subscription-
questions) - the shared `claude` CLI subprocess mechanism factored out of
feature/resume-reasoner-path's reasoner_pipeline.py so
tailoring.discuss_and_draft's opening-question generation can reuse the
exact same subscription-covered call, not a similar-looking
reimplementation. Mocks subprocess.Popen directly - never actually shells
out to a real `claude` CLI in this suite.

2026-08-17 (real PID/timing tracking): run_claude_cli() switched from
subprocess.run to subprocess.Popen so a caller can observe the real PID
via on_start() before the call blocks waiting for the process to finish -
subprocess.run only ever hands back a result after the process has
already exited, too late to ever report a still-running PID. Tests below
mock Popen accordingly, and add explicit coverage for on_start() actually
firing with the fake process's pid, and for a timeout killing the
still-running child rather than leaving it orphaned."""

import json
import subprocess

import pytest

import tailoring.reasoner_cli as reasoner_cli


class _FakePopen:
    """Stands in for subprocess.Popen: __init__ takes the same call
    reasoner_cli.py makes, .pid is available immediately (matching real
    Popen), and .communicate()/.kill() behave like the real thing for the
    scenarios these tests need."""

    def __init__(self, pid=12345, stdout="", stderr="", returncode=0, raise_on_communicate=None):
        self.pid = pid
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._raise_on_communicate = raise_on_communicate
        self.killed = False
        self.communicate_calls = 0

    def communicate(self, input=None, timeout=None):
        self.communicate_calls += 1
        if self._raise_on_communicate is not None and self.communicate_calls == 1:
            raise self._raise_on_communicate
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


def _patch_popen(monkeypatch, fake_popen):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake_popen)


def test_run_claude_cli_returns_result_text_on_success(monkeypatch):
    envelope = json.dumps({"is_error": False, "result": "hello from claude"})
    _patch_popen(monkeypatch, _FakePopen(stdout=envelope))

    result = reasoner_cli.run_claude_cli("some prompt")

    assert result == "hello from claude"


def test_run_claude_cli_passes_utf8_encoding_to_popen(monkeypatch):
    """Real bug found live 2026-08-17 (feature/jd-keyword-taxonomy-gaps):
    a real scraped job posting containing a Unicode character outside
    cp1252 (e.g. "○") crashed the stdin writer thread with
    UnicodeEncodeError before the `claude` CLI ever ran, because plain
    text=True let Python pick the console's own codepage instead of
    UTF-8. Asserts the real fix (encoding="utf-8" passed explicitly) is
    in place, not just that a mocked call succeeds - a test using ASCII-
    only prompt text would pass identically whether or not encoding is
    pinned, so it wouldn't actually catch a regression of this fix."""
    captured_kwargs = {}

    def _fake_popen(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return _FakePopen(stdout=json.dumps({"is_error": False, "result": "ok"}))

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    reasoner_cli.run_claude_cli("prompt with a real Unicode bullet: ○")

    assert captured_kwargs.get("encoding") == "utf-8"


def test_run_claude_cli_fires_on_start_with_real_pid(monkeypatch):
    """2026-08-17: on_start(pid) is the whole point of the Popen switch -
    a caller needs the real OS PID while the subprocess is still running
    so it can be persisted (applications.set_qa_status's pid/started_at)
    for the Task Monitor view. Must fire with the actual .pid Popen
    reports, before/around the blocking communicate() call, not some
    placeholder."""
    _patch_popen(monkeypatch, _FakePopen(pid=99999, stdout=json.dumps({"is_error": False, "result": "ok"})))
    seen_pids = []

    reasoner_cli.run_claude_cli("some prompt", on_start=seen_pids.append)

    assert seen_pids == [99999]


def test_run_claude_cli_never_calls_on_start_when_cli_missing(monkeypatch):
    def _raise_not_found(*a, **k):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(subprocess, "Popen", _raise_not_found)
    seen_pids = []

    with pytest.raises(reasoner_cli.ReasonerUnavailable):
        reasoner_cli.run_claude_cli("some prompt", on_start=seen_pids.append)

    assert seen_pids == []


def test_run_claude_cli_raises_reasoner_unavailable_when_cli_missing(monkeypatch):
    def _raise_not_found(*a, **k):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(subprocess, "Popen", _raise_not_found)

    with pytest.raises(reasoner_cli.ReasonerUnavailable):
        reasoner_cli.run_claude_cli("some prompt")


def test_run_claude_cli_raises_runtime_error_on_timeout(monkeypatch):
    fake = _FakePopen(raise_on_communicate=subprocess.TimeoutExpired(cmd="claude", timeout=300))
    _patch_popen(monkeypatch, fake)

    with pytest.raises(RuntimeError, match="timed out"):
        reasoner_cli.run_claude_cli("some prompt", timeout_seconds=300)


def test_run_claude_cli_kills_process_on_timeout(monkeypatch):
    """2026-08-17: unlike subprocess.run, Popen.communicate() leaves the
    child process running after a TimeoutExpired - without an explicit
    kill(), a timed-out reasoner call would leak an orphaned `claude` OS
    process with nothing left tracking it, exactly the untracked-zombie
    problem this whole feature exists to prevent."""
    fake = _FakePopen(raise_on_communicate=subprocess.TimeoutExpired(cmd="claude", timeout=300))
    _patch_popen(monkeypatch, fake)

    with pytest.raises(RuntimeError):
        reasoner_cli.run_claude_cli("some prompt", timeout_seconds=300)

    assert fake.killed is True


def test_run_claude_cli_raises_runtime_error_on_nonzero_exit(monkeypatch):
    _patch_popen(monkeypatch, _FakePopen(returncode=1, stdout="", stderr="boom"))

    with pytest.raises(RuntimeError, match="exited with code 1"):
        reasoner_cli.run_claude_cli("some prompt")


def test_run_claude_cli_raises_runtime_error_on_non_json_stdout(monkeypatch):
    _patch_popen(monkeypatch, _FakePopen(stdout="not json at all"))

    with pytest.raises(RuntimeError, match="non-JSON"):
        reasoner_cli.run_claude_cli("some prompt")


def test_run_claude_cli_raises_runtime_error_when_envelope_reports_error(monkeypatch):
    envelope = json.dumps({"is_error": True, "result": "refused"})
    _patch_popen(monkeypatch, _FakePopen(stdout=envelope))

    with pytest.raises(RuntimeError, match="reported an error"):
        reasoner_cli.run_claude_cli("some prompt")


def test_parse_json_reply_handles_bare_json():
    assert reasoner_cli.parse_json_reply('{"a": 1}') == {"a": 1}


def test_parse_json_reply_handles_markdown_fence():
    text = 'Sure, here it is:\n```json\n{"a": 1}\n```'
    assert reasoner_cli.parse_json_reply(text) == {"a": 1}


def test_parse_json_reply_handles_stray_surrounding_text():
    text = 'Here is the object {"a": 1} - hope that helps!'
    assert reasoner_cli.parse_json_reply(text) == {"a": 1}


def test_parse_json_reply_raises_when_no_json_object_found():
    with pytest.raises(RuntimeError, match="Could not find a JSON object"):
        reasoner_cli.parse_json_reply("no json here at all")
