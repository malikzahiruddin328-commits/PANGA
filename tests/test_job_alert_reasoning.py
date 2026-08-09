"""Tests for src/tailoring/job_alert_reasoning.py's max_tokens escalation
(added 2026-08-08, fixing a real gap: the old hardcoded max_tokens=1500
truncated on any digest bundling more than ~6-8 listings). call_structured
is monkeypatched here - no live Anthropic calls in the regression suite,
same scope as every other direct-API module's tests."""

import pytest

import llm_client
import tailoring.job_alert_reasoning as job_alert_reasoning


def test_succeeds_on_first_tier_when_not_truncated(monkeypatch):
    captured = []

    def fake_call_structured(client, **kwargs):
        captured.append(kwargs["max_tokens"])
        return {"listings": [{"title": "CIO", "organization": "Acme", "location": "", "posting_url": "https://x.com", "description": ""}]}
    monkeypatch.setattr(job_alert_reasoning, "call_structured", fake_call_structured)
    monkeypatch.setattr(job_alert_reasoning, "get_client", lambda: object())

    listings = job_alert_reasoning.extract_listings("Subject", "Body")

    assert len(listings) == 1
    assert captured == [8000]  # never escalated - first tier was enough


def test_escalates_through_tiers_on_truncation_then_succeeds(monkeypatch):
    captured = []

    def fake_call_structured(client, **kwargs):
        captured.append(kwargs["max_tokens"])
        if kwargs["max_tokens"] < 32000:
            raise llm_client.LLMResponseTruncated("cut off")
        return {"listings": [{"title": "CIO", "organization": "Acme", "location": "", "posting_url": "https://x.com", "description": ""}]}
    monkeypatch.setattr(job_alert_reasoning, "call_structured", fake_call_structured)
    monkeypatch.setattr(job_alert_reasoning, "get_client", lambda: object())

    listings = job_alert_reasoning.extract_listings("Subject", "Body")

    assert len(listings) == 1
    assert captured == [8000, 16000, 32000]  # escalated through every tier


def test_raises_after_largest_tier_still_truncates(monkeypatch):
    def always_truncates(client, **kwargs):
        raise llm_client.LLMResponseTruncated("cut off")
    monkeypatch.setattr(job_alert_reasoning, "call_structured", always_truncates)
    monkeypatch.setattr(job_alert_reasoning, "get_client", lambda: object())

    with pytest.raises(llm_client.LLMResponseTruncated):
        job_alert_reasoning.extract_listings("Subject", "Body")


def test_non_truncation_failure_is_not_retried(monkeypatch):
    calls = []

    def refuses(client, **kwargs):
        calls.append(1)
        raise llm_client.LLMCallFailed("Claude declined to respond.")
    monkeypatch.setattr(job_alert_reasoning, "call_structured", refuses)
    monkeypatch.setattr(job_alert_reasoning, "get_client", lambda: object())

    with pytest.raises(llm_client.LLMCallFailed):
        job_alert_reasoning.extract_listings("Subject", "Body")
    assert calls == [1]  # a refusal isn't a token-budget problem - no point retrying with more tokens
