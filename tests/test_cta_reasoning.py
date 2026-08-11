"""Tests for tailoring/cta_reasoning.py's organization-hint pre-filter
(2026-08-09) - real gap found live: a rejection email that literally
named its own organization was matched to a completely unrelated
candidate at a different company, purely on loose title-word overlap.
_organization_hint() is pure/deterministic (no API call), tested
directly. match_cta_application()/match_application_confirmation()
themselves are covered indirectly here (verifying the hint text reaches
call_structured's content) - call_structured is monkeypatched, no live
Anthropic calls, same scope as every other direct-API module's tests."""

import tailoring.cta_reasoning as cta_reasoning


def test_organization_hint_fires_when_exactly_one_candidate_matches():
    body = "UAB Medicine emailed 2026-08-08: not selected for the Chief Digital and Information Officer - UABHS position."
    candidates = [
        {"source": "Indeed", "job_id": "15c988aae0597b04", "title": "Director, Digital Solutions Architect", "organization": "BD", "location": "Franklin Lakes, NJ"},
        {"source": "Indeed", "job_id": "aa9748nxxdy4", "title": "Chief Digital and Information Officer - UABHS", "organization": "UAB Medicine", "location": "Birmingham, AL"},
    ]
    hint = cta_reasoning._organization_hint(body, candidates)
    assert "UAB Medicine" in hint
    assert "aa9748nxxdy4" in hint
    assert "15c988aae0597b04" not in hint


def test_organization_hint_empty_when_no_candidate_organization_appears():
    body = "Thank you for your interest. We have decided to move forward with other candidates."
    candidates = [
        {"source": "Indeed", "job_id": "1", "title": "Director", "organization": "Acme Corp", "location": ""},
        {"source": "Indeed", "job_id": "2", "title": "VP", "organization": "Beta Inc", "location": ""},
    ]
    assert cta_reasoning._organization_hint(body, candidates) == ""


def test_organization_hint_empty_when_multiple_candidates_match():
    # Both organizations mentioned (e.g. a forwarded/quoted thread) -
    # genuinely ambiguous, left to the LLM rather than guessing which one.
    body = "This is unrelated to both Acme Corp and Beta Inc, just testing both names appear."
    candidates = [
        {"source": "Indeed", "job_id": "1", "title": "Director", "organization": "Acme Corp", "location": ""},
        {"source": "Indeed", "job_id": "2", "title": "VP", "organization": "Beta Inc", "location": ""},
    ]
    assert cta_reasoning._organization_hint(body, candidates) == ""


def test_organization_hint_skips_short_generic_names():
    # "BD" is too short/generic to safely substring-match - a false
    # positive here would be worse than no hint at all.
    body = "This email mentions bd somewhere in unrelated text."
    candidates = [
        {"source": "Indeed", "job_id": "1", "title": "Director", "organization": "BD", "location": ""},
    ]
    assert cta_reasoning._organization_hint(body, candidates) == ""


def test_organization_hint_handles_blank_organization():
    body = "Some email body."
    candidates = [
        {"source": "Indeed", "job_id": "1", "title": "Director", "organization": "", "location": ""},
        {"source": "Indeed", "job_id": "2", "title": "VP", "organization": None, "location": ""},
    ]
    assert cta_reasoning._organization_hint(body, candidates) == ""


def test_organization_hint_case_insensitive():
    body = "we regret to inform you that acme corp has selected another candidate."
    candidates = [
        {"source": "Indeed", "job_id": "1", "title": "Director", "organization": "Acme Corp", "location": ""},
    ]
    hint = cta_reasoning._organization_hint(body, candidates)
    assert "Acme Corp" in hint


def test_match_cta_application_includes_hint_in_call_content(monkeypatch):
    captured = {}

    def fake_call_structured(client, **kwargs):
        captured["content"] = kwargs["user_content"]
        return {"matched": False, "source": "", "job_id": "", "reason": ""}
    monkeypatch.setattr(cta_reasoning, "call_structured", fake_call_structured)
    monkeypatch.setattr(cta_reasoning, "get_client", lambda: object())

    body = "UAB Medicine emailed: not selected for the Chief Digital and Information Officer position."
    candidates = [
        {"source": "Indeed", "job_id": "aa9748nxxdy4", "title": "Chief Digital and Information Officer - UABHS", "organization": "UAB Medicine", "location": ""},
    ]
    cta_reasoning.match_cta_application("rejection", {"subject": "Update", "sender": "hr@uabmedicine.org"}, body, candidates)

    assert "DETERMINISTIC HINT" in captured["content"]
    assert "UAB Medicine" in captured["content"]


def test_match_application_confirmation_includes_hint_in_call_content(monkeypatch):
    captured = {}

    def fake_call_structured(client, **kwargs):
        captured["content"] = kwargs["user_content"]
        return {"matched": False, "source": "", "job_id": "", "reason": ""}
    monkeypatch.setattr(cta_reasoning, "call_structured", fake_call_structured)
    monkeypatch.setattr(cta_reasoning, "get_client", lambda: object())

    body = "Thank you for applying to Acme Corp. We've received your application."
    candidates = [
        {"source": "Indeed", "job_id": "1", "title": "Director", "organization": "Acme Corp", "location": ""},
    ]
    cta_reasoning.match_application_confirmation({"subject": "Application received", "sender": "no-reply@acme.com"}, body, candidates)

    assert "DETERMINISTIC HINT" in captured["content"]
