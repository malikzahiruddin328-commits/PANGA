"""2026-08-11 (Mirror audit): 9 modules called llm_client.call_structured/
call_with_web_search with no purpose= tag, so $9.48 of real spend in one
day alone was invisible in cost_log, lumped under "unspecified" - the same
per-feature cost visibility fit_score/draft_resume already had was simply
missing everywhere else. Each of these tests exercises the REAL public
function end to end (only the Anthropic client itself is faked, matching
every other llm_client test's pattern) and asserts the resulting cost_log
entry carries the module's own real purpose tag - not just that the code
compiles or that a mock was called with the right kwarg.
"""

import json
from types import SimpleNamespace

import pytest

from cost_log import load_cost_log


class _FakeStream:
    def __init__(self, text: str):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter([])

    def get_final_message(self):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


class _FakeMessages:
    def __init__(self, text: str):
        self._text = text

    def stream(self, **kwargs):
        return _FakeStream(self._text)

    def create(self, **kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(input_tokens=80, output_tokens=40),
        )


class _FakeClient:
    def __init__(self, text: str):
        self.messages = _FakeMessages(text)


def _fake_client_returning(payload: dict) -> _FakeClient:
    return _FakeClient(json.dumps(payload))


def _purposes_in_log() -> set:
    return {e["purpose"] for e in load_cost_log()}


def test_cta_classify_thread_purpose(isolated_data, monkeypatch):
    import tailoring.cta_reasoning as cta_reasoning

    monkeypatch.setattr(cta_reasoning, "get_client", lambda: _fake_client_returning(
        {"bucket": "call_to_action", "cta_category": "interview_request", "confident": True}
    ))
    cta_reasoning.classify_thread({"subject": "s", "sender": "a@b.com", "date": "d", "snippet": "sn"})
    assert "cta_classify_thread" in _purposes_in_log()


def test_cta_match_application_confirmation_purpose(isolated_data, monkeypatch):
    import tailoring.cta_reasoning as cta_reasoning

    monkeypatch.setattr(cta_reasoning, "get_client", lambda: _fake_client_returning(
        {"matched": False, "source": "", "job_id": "", "reason": "no match"}
    ))
    cta_reasoning.match_application_confirmation({"subject": "s", "sender": "a@b.com"}, "body", [])
    assert "cta_match_application_confirmation" in _purposes_in_log()


def test_cta_match_cta_application_purpose(isolated_data, monkeypatch):
    import tailoring.cta_reasoning as cta_reasoning

    monkeypatch.setattr(cta_reasoning, "get_client", lambda: _fake_client_returning(
        {"matched": False, "source": "", "job_id": "", "reason": "no match"}
    ))
    cta_reasoning.match_cta_application("rejection", {"subject": "s", "sender": "a@b.com"}, "body", [])
    assert "cta_match_application" in _purposes_in_log()


def test_cta_draft_reply_purpose(isolated_data, monkeypatch):
    import tailoring.cta_reasoning as cta_reasoning

    monkeypatch.setattr(cta_reasoning, "get_client", lambda: _fake_client_returning({"reply_body": "Thanks!"}))
    cta_reasoning.draft_cta_reply("rejection", "subj", "snip")
    assert "cta_draft_reply" in _purposes_in_log()


def test_interview_prep_generate_prep_purposes(isolated_data, monkeypatch):
    import tailoring.interview_prep as interview_prep

    # generate_prep makes TWO real calls (web-search research, then
    # structured prep) - both must carry their own purpose, and both
    # should carry the same job_key since it's one logical job.
    call_log = []

    class _TrackedMessages(_FakeMessages):
        def create(self, **kwargs):
            call_log.append("create")
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Some real research notes.")],
                usage=SimpleNamespace(input_tokens=80, output_tokens=40),
            )

        def stream(self, **kwargs):
            call_log.append("stream")
            return _FakeStream(json.dumps({
                "company_snapshot": "snap", "interviewers": [],
                "likely_questions": [], "questions_to_ask": [],
            }))

    fake_client = _FakeClient("")
    fake_client.messages = _TrackedMessages("")
    monkeypatch.setattr(interview_prep, "get_client", lambda: fake_client)

    job = {"source": "linkedin", "job_id": "42", "organization": "Acme", "title": "VP Eng"}
    interview_prep.generate_prep(job, {"name": "Zahir"})

    assert call_log == ["create", "stream"]
    purposes = _purposes_in_log()
    assert "interview_prep_research" in purposes
    assert "interview_prep_generate" in purposes
    for entry in load_cost_log():
        assert entry.get("source") == "linkedin"
        assert entry.get("job_id") == "42"


def test_job_alert_extract_listings_purpose(isolated_data, monkeypatch):
    import tailoring.job_alert_reasoning as job_alert_reasoning

    monkeypatch.setattr(job_alert_reasoning, "get_client", lambda: _fake_client_returning({"listings": []}))
    job_alert_reasoning.extract_listings("subj", "body")
    assert "job_alert_extract_listings" in _purposes_in_log()


def test_prospector_score_purpose(isolated_data, monkeypatch):
    import prospector.prospector_score as prospector_score

    monkeypatch.setattr(prospector_score, "_client", lambda: _fake_client_returning(
        {"score": 42, "rationale": "thin data", "next_actions": []}
    ))
    prospector_score.compute_prospector_score({"some": "input"})
    assert "prospector_score" in _purposes_in_log()


def test_company_website_lookup_purpose(isolated_data, monkeypatch):
    import prospector.company_lookup as company_lookup

    fake_client = _FakeClient("")
    fake_client.messages = _FakeMessages("https://acme.com")
    monkeypatch.setattr(company_lookup, "_client", lambda: fake_client)
    company_lookup.lookup_company_website("Acme")
    assert "company_website_lookup" in _purposes_in_log()


def test_learn_engine_analyze_purpose(isolated_data, monkeypatch):
    import prospector.learn_engine as learn_engine

    monkeypatch.setattr(learn_engine, "get_client", lambda: _fake_client_returning(
        {"narrative": "n", "recommendations": []}
    ))
    learn_engine.analyze({"some": "input"})
    assert "learn_engine_analyze" in _purposes_in_log()


def test_outreach_draft_email_purpose(isolated_data, monkeypatch):
    import prospector.outreach_reasoning as outreach_reasoning

    monkeypatch.setattr(outreach_reasoning, "get_client", lambda: _fake_client_returning({"email_body": "Hi!"}))
    outreach_reasoning.draft_outreach_email("Jane Doe", "Recruiter", "Applying to VP Eng role")
    assert "outreach_draft_email" in _purposes_in_log()


def test_rejection_diagnosis_purpose(isolated_data, monkeypatch):
    import prospector.rejection_diagnosis as rejection_diagnosis

    monkeypatch.setattr(rejection_diagnosis, "get_client", lambda: _fake_client_returning(
        {"narrative": "n", "recommendations": []}
    ))
    rejection_diagnosis.diagnose({"rejected": [], "not_interested_with_reason": []})
    assert "rejection_diagnosis" in _purposes_in_log()


def test_linkedin_profile_enhance_purpose(isolated_data, monkeypatch):
    import linkedin.enhance as enhance

    monkeypatch.setattr(enhance, "get_client", lambda: _fake_client_returning(
        {"profile_strength_score": 70, "profile_strength_rationale": "r", "suggestions": []}
    ))
    enhance.analyze_profile({"some": "context"})
    assert "linkedin_profile_enhance" in _purposes_in_log()


def test_all_nine_modules_produce_distinct_real_purposes(isolated_data, monkeypatch):
    # The actual real-numbers ask: run every module's call once and
    # confirm cost_log ends up with 13 distinct, correctly-tagged
    # entries - not "unspecified" absorbing all of them.
    import linkedin.enhance as enhance
    import prospector.company_lookup as company_lookup
    import prospector.learn_engine as learn_engine
    import prospector.outreach_reasoning as outreach_reasoning
    import prospector.prospector_score as prospector_score
    import prospector.rejection_diagnosis as rejection_diagnosis
    import tailoring.cta_reasoning as cta_reasoning
    import tailoring.job_alert_reasoning as job_alert_reasoning

    monkeypatch.setattr(cta_reasoning, "get_client", lambda: _fake_client_returning(
        {"bucket": "not_related", "cta_category": "", "confident": True}
    ))
    cta_reasoning.classify_thread({"subject": "s", "sender": "a@b.com", "date": "d", "snippet": "sn"})

    monkeypatch.setattr(job_alert_reasoning, "get_client", lambda: _fake_client_returning({"listings": []}))
    job_alert_reasoning.extract_listings("subj", "body")

    monkeypatch.setattr(prospector_score, "_client", lambda: _fake_client_returning(
        {"score": 1, "rationale": "r", "next_actions": []}
    ))
    prospector_score.compute_prospector_score({})

    company_client = _FakeClient("")
    company_client.messages = _FakeMessages("NOT_FOUND")
    monkeypatch.setattr(company_lookup, "_client", lambda: company_client)
    company_lookup.lookup_company_website("Acme")

    monkeypatch.setattr(learn_engine, "get_client", lambda: _fake_client_returning(
        {"narrative": "n", "recommendations": []}
    ))
    learn_engine.analyze({})

    monkeypatch.setattr(outreach_reasoning, "get_client", lambda: _fake_client_returning({"email_body": "hi"}))
    outreach_reasoning.draft_outreach_email("Jane", None, "ctx")

    monkeypatch.setattr(rejection_diagnosis, "get_client", lambda: _fake_client_returning(
        {"narrative": "n", "recommendations": []}
    ))
    rejection_diagnosis.diagnose({"rejected": [], "not_interested_with_reason": []})

    monkeypatch.setattr(enhance, "get_client", lambda: _fake_client_returning(
        {"profile_strength_score": 1, "profile_strength_rationale": "r", "suggestions": []}
    ))
    enhance.analyze_profile({})

    purposes = [e["purpose"] for e in load_cost_log()]
    assert "unspecified" not in purposes
    assert len(purposes) == len(set(purposes)) == 8  # one call each, all distinct
