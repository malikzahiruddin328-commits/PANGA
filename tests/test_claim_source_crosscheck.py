"""tailoring.claim_source_crosscheck (2026-08-19) - real fix for Zahir's
live complaint: he was asked to re-confirm 4 employment dates that were
already stated verbatim in his own ingested resume. See that module's own
top docstring for the full root-cause writeup. Mocks reasoner_cli.
run_claude_cli at the module seam this module actually calls, same pattern
as test_gap_question_phrasing.py."""

import json

import tailoring.claim_source_crosscheck as csc
from tailoring.reasoner_cli import ReasonerUnavailable

MARKERS = [
    {"field": "resume_text", "skill": "date", "line": "September 2018 - January 2026?"},
    {"field": "resume_text", "skill": "budget", "line": "peak-year technology budget of $15 million? (2020)."},
]
SOURCE_TEXT = "=== resume: real.docx ===\nSK Life Science, Inc.\t09/2018 - 01/2026\nHead of IT"


def test_empty_markers_returns_empty_without_calling_reasoner(monkeypatch):
    calls = []
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: calls.append(1) or "{}")
    result = csc.crosscheck_claims_against_source([], SOURCE_TEXT)
    assert result == {}
    assert not calls


def test_verifiable_claim_is_resolved(monkeypatch):
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: json.dumps({
        "resolved": [{"index": 0, "resolved_line": "September 2018 - January 2026"}],
    }))
    result = csc.crosscheck_claims_against_source(MARKERS, SOURCE_TEXT)
    assert result == {0: "September 2018 - January 2026"}


def test_unverifiable_claim_is_simply_omitted(monkeypatch):
    # The reasoner only resolves index 0 (the date, genuinely in the source)
    # and correctly leaves index 1 (the budget figure, not in the source)
    # out of its reply entirely - never a guessed number for it.
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: json.dumps({
        "resolved": [{"index": 0, "resolved_line": "September 2018 - January 2026"}],
    }))
    result = csc.crosscheck_claims_against_source(MARKERS, SOURCE_TEXT)
    assert 1 not in result


def test_a_resolved_line_still_containing_a_hedge_is_rejected(monkeypatch):
    # Same non-negotiable bar unconfirmed_claims.resolve_unconfirmed_claim()
    # itself enforces (raises ValueError on this) - a "resolution" that
    # still has "?" in it isn't actually resolved.
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: json.dumps({
        "resolved": [{"index": 0, "resolved_line": "September 2018 - January 2026?"}],
    }))
    result = csc.crosscheck_claims_against_source(MARKERS, SOURCE_TEXT)
    assert result == {}


def test_out_of_range_index_is_ignored(monkeypatch):
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: json.dumps({
        "resolved": [{"index": 99, "resolved_line": "Whatever"}],
    }))
    result = csc.crosscheck_claims_against_source(MARKERS, SOURCE_TEXT)
    assert result == {}


def test_empty_resolved_line_is_ignored(monkeypatch):
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: json.dumps({
        "resolved": [{"index": 0, "resolved_line": "   "}],
    }))
    result = csc.crosscheck_claims_against_source(MARKERS, SOURCE_TEXT)
    assert result == {}


def test_reasoner_unavailable_fails_soft_to_empty(monkeypatch):
    def _raise(*a, **k):
        raise ReasonerUnavailable("not logged in")
    monkeypatch.setattr(csc, "run_claude_cli", _raise)
    result = csc.crosscheck_claims_against_source(MARKERS, SOURCE_TEXT)
    assert result == {}


def test_malformed_reply_fails_soft_to_empty(monkeypatch):
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: "not json at all")
    result = csc.crosscheck_claims_against_source(MARKERS, SOURCE_TEXT)
    assert result == {}


def test_prompt_includes_source_text_and_flagged_lines(monkeypatch):
    captured = {}

    def _fake_run(prompt, timeout_seconds=None, on_start=None):
        captured["prompt"] = prompt
        return json.dumps({"resolved": []})

    monkeypatch.setattr(csc, "run_claude_cli", _fake_run)
    csc.crosscheck_claims_against_source(MARKERS, SOURCE_TEXT)
    assert SOURCE_TEXT in captured["prompt"]
    assert "September 2018 - January 2026?" in captured["prompt"]
    assert "peak-year technology budget" in captured["prompt"]
