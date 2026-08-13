"""tailoring.reasoner_cli (2026-08-13, fix/discuss-draft-subscription-
questions) - the shared `claude` CLI subprocess mechanism factored out of
feature/resume-reasoner-path's reasoner_pipeline.py so
tailoring.discuss_and_draft's opening-question generation can reuse the
exact same subscription-covered call, not a similar-looking
reimplementation. Mocks subprocess.run directly - never actually shells
out to a real `claude` CLI in this suite."""

import json
import subprocess

import pytest

import tailoring.reasoner_cli as reasoner_cli


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_claude_cli_returns_result_text_on_success(monkeypatch):
    envelope = json.dumps({"is_error": False, "result": "hello from claude"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess(0, envelope, ""))

    result = reasoner_cli.run_claude_cli("some prompt")

    assert result == "hello from claude"


def test_run_claude_cli_raises_reasoner_unavailable_when_cli_missing(monkeypatch):
    def _raise_not_found(*a, **k):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(subprocess, "run", _raise_not_found)

    with pytest.raises(reasoner_cli.ReasonerUnavailable):
        reasoner_cli.run_claude_cli("some prompt")


def test_run_claude_cli_raises_runtime_error_on_timeout(monkeypatch):
    def _raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=300)
    monkeypatch.setattr(subprocess, "run", _raise_timeout)

    with pytest.raises(RuntimeError, match="timed out"):
        reasoner_cli.run_claude_cli("some prompt", timeout_seconds=300)


def test_run_claude_cli_raises_runtime_error_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess(1, "", "boom"))

    with pytest.raises(RuntimeError, match="exited with code 1"):
        reasoner_cli.run_claude_cli("some prompt")


def test_run_claude_cli_raises_runtime_error_on_non_json_stdout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess(0, "not json at all", ""))

    with pytest.raises(RuntimeError, match="non-JSON"):
        reasoner_cli.run_claude_cli("some prompt")


def test_run_claude_cli_raises_runtime_error_when_envelope_reports_error(monkeypatch):
    envelope = json.dumps({"is_error": True, "result": "refused"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess(0, envelope, ""))

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
