"""2026-08-13: real cost_log data showed request_additional_gap_questions()
("Answer more questions") had 15 real production calls averaging 54,990
input tokens each ($5.35 total) with zero cache_creation_input_tokens/
cache_read_input_tokens ever logged - the full candidate profile JSON was
being re-sent at full price on every call, same leak already fixed for
score_job() (see test_score_job_prompt_caching.py) and generate_documents()'s
shared_context on 2026-08-11. request_additional_gap_questions() now marks
the profile as its own cache_control: ephemeral system content block,
identical shape to score_job's fix, moving only the job posting and the
"already covered" list (both genuinely different per call) into
user_content. These tests cover the real request shape this function now
builds - not the dollar math, which is api_cost.py's job.
"""

from tailoring import drafting


PROFILE = {"name": "Zahir", "skills": ["Enterprise Architecture", "IT Governance"], "gap_interview_answers": []}
JOB = {"source": "linkedin", "job_id": "1", "title": "VP Information Technology", "organization": "Acme",
       "ats_required_keywords": [], "ats_preferred_keywords": []}
APP_RECORD = {"resume_text": "SKILLS\nPython", "resume_clarifying_questions": []}


def _capture_call(monkeypatch):
    captured = {}

    def _fake_call_structured(client, system, user_content, schema, max_tokens, **kwargs):
        captured["system"] = system
        captured["user_content"] = user_content
        return {"clarifying_questions": []}

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    drafting.request_additional_gap_questions(JOB, PROFILE, APP_RECORD)
    return captured


def test_system_is_a_content_block_list_not_a_plain_string(monkeypatch):
    captured = _capture_call(monkeypatch)
    assert isinstance(captured["system"], list)
    assert all(isinstance(block, dict) for block in captured["system"])


def test_profile_block_is_marked_cache_control_ephemeral(monkeypatch):
    captured = _capture_call(monkeypatch)
    profile_blocks = [b for b in captured["system"] if "cache_control" in b]
    assert len(profile_blocks) == 1
    assert profile_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "Zahir" in profile_blocks[0]["text"]
    assert "CANDIDATE'S MASTER PROFILE" in profile_blocks[0]["text"]


def test_answer_more_system_prompt_block_is_not_cached(monkeypatch):
    captured = _capture_call(monkeypatch)
    prompt_blocks = [b for b in captured["system"] if "cache_control" not in b]
    assert len(prompt_blocks) == 1
    assert prompt_blocks[0]["text"] == drafting._ANSWER_MORE_SYSTEM_PROMPT


def test_job_posting_and_already_covered_moved_into_user_content_alone(monkeypatch):
    captured = _capture_call(monkeypatch)
    assert "JOB POSTING" in captured["user_content"]
    assert "VP Information Technology" in captured["user_content"]
    assert "ALREADY COVERED" in captured["user_content"]
    assert "Zahir" not in captured["user_content"]  # profile no longer duplicated into user_content


def test_request_additional_gap_questions_still_returns_the_real_result(monkeypatch):
    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(
        drafting, "call_structured",
        lambda client, system, user_content, schema, max_tokens, **kwargs: {
            "clarifying_questions": [
                {"type": "skill_gap", "skill": "Team size at Acme", "question": "How big was the team?", "suggested_answer": ""},
            ]
        },
    )
    result = drafting.request_additional_gap_questions(JOB, PROFILE, APP_RECORD)
    assert result["added_count"] == 1
    assert result["new_questions"][0]["skill"] == "Team size at Acme"
