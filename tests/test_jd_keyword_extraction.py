"""tailoring.jd_keyword_extraction (2026-08-17, feature/jd-keyword-
taxonomy-gaps, Phase 1) - standalone $0 subscription-covered ATS keyword
extraction for a job posting on its own. No live network/subprocess calls
- run_claude_cli is mocked in every test."""

import json

import tailoring.jd_keyword_extraction as jke


JOB = {
    "source": "linkedin", "job_id": "1", "title": "VP IT",
    "description": "We require SQL, AWS, and 8+ years of IT leadership experience. Preferred: PMP certification.",
}
EMPTY_JOB = {"source": "linkedin", "job_id": "2", "title": "", "description": ""}


def test_posting_text_for_joins_title_qualification_summary_description():
    job = {"title": "VP IT", "qualification_summary": "Summary text", "description": "Description text"}
    text = jke.posting_text_for(job)
    assert "VP IT" in text
    assert "Summary text" in text
    assert "Description text" in text


def test_extract_keywords_via_subscription_short_circuits_on_no_posting_text():
    required, preferred = jke.extract_keywords_via_subscription(EMPTY_JOB)
    assert required == []
    assert preferred == []


def test_extract_keywords_via_subscription_calls_cli_and_applies_backstops(monkeypatch):
    calls = []

    def _fake_run_claude_cli(prompt, timeout_seconds=None):
        calls.append(prompt)
        return json.dumps({
            # "8+ years" should be dropped by the years-experience backstop,
            # "SQL"/"AWS" should survive untouched - same real backstop
            # pipeline the paid path (_extract_ats_keywords) applies.
            "required_keywords": ["SQL", "AWS", "8+ years"],
            "preferred_keywords": ["PMP certification"],
        })

    monkeypatch.setattr(jke, "run_claude_cli", _fake_run_claude_cli)

    required, preferred = jke.extract_keywords_via_subscription(JOB)

    assert len(calls) == 1
    assert "SQL" in calls[0] or "extracting the literal keywords" in calls[0].lower()
    assert required == ["SQL", "AWS"]
    assert preferred == ["PMP certification"]


def test_extraction_prompt_uses_the_real_ats_keywords_system_prompt():
    prompt = jke._extraction_prompt("some posting text")
    assert "extracting the literal keywords" in prompt.lower()
    assert "some posting text" in prompt


def test_extractor_version_matches_drafting_module():
    from tailoring.drafting import ATS_KEYWORDS_EXTRACTOR_VERSION

    assert jke.EXTRACTOR_VERSION == ATS_KEYWORDS_EXTRACTOR_VERSION
