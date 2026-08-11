"""2026-08-11, Zahir's cost-review directive: fit_score's paid Opus call was
re-sending the full ~60K-token profile fresh on every single call, with no
prompt caching at all, despite CLAUDE.md's own cost principle calling for
exactly this ("use prompt caching for content that repeats across calls,
e.g. a profile embedded in every scoring call"). score_job() now marks the
profile as its own cache_control: ephemeral system content block, moving
only the job posting (genuinely different every call) into user_content.
These tests cover the real request shape score_job() builds - not the
dollar math, which is api_cost.py's job (see test_api_cost.py's new cache-
pricing tests) - to prove the caching restructure is wired correctly.
"""

from tailoring import drafting


PROFILE = {"name": "Zahir", "skills": ["Enterprise Architecture", "IT Governance"]}
JOB = {"source": "linkedin", "job_id": "42", "title": "VP Information Technology", "organization": "Acme"}


def _capture_call(monkeypatch):
    captured = {}

    def _fake_call_structured(client, system, user_content, schema, max_tokens, **kwargs):
        captured["system"] = system
        captured["user_content"] = user_content
        return {"fit_score": 77, "fit_rationale": "Strong match."}

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    drafting.score_job(JOB, PROFILE)
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


def test_score_system_prompt_block_is_not_cached(monkeypatch):
    # Only the profile is large/repeated enough to be worth caching - the
    # short, fixed rubric text has no real cost benefit from a cache
    # breakpoint of its own, so it stays a plain uncached block.
    captured = _capture_call(monkeypatch)
    prompt_blocks = [b for b in captured["system"] if "cache_control" not in b]
    assert len(prompt_blocks) == 1
    assert prompt_blocks[0]["text"] == drafting.SCORE_SYSTEM_PROMPT


def test_job_posting_moved_into_user_content_alone(monkeypatch):
    captured = _capture_call(monkeypatch)
    assert "JOB POSTING" in captured["user_content"]
    assert "VP Information Technology" in captured["user_content"]
    assert "Zahir" not in captured["user_content"]  # profile no longer duplicated into user_content


def test_score_job_still_returns_the_real_fit_score(monkeypatch):
    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(
        drafting, "call_structured",
        lambda client, system, user_content, schema, max_tokens, **kwargs: {"fit_score": 88, "fit_rationale": "Great fit."},
    )
    result = drafting.score_job(JOB, PROFILE)
    assert result == {"fit_score": 88, "fit_rationale": "Great fit."}
