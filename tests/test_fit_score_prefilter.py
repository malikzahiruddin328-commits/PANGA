import pytest

from llm_client import LLMCallFailed, LLMNotConfigured
from security.crypto_store import read_json
from tailoring import fit_score_prefilter


def _job(title, source="linkedin", job_id="1", department=None, organization="Acme"):
    job = {"source": source, "job_id": job_id, "title": title, "organization": organization}
    if department is not None:
        job["department"] = department
    return job


PROFILE = {"target_title_framings": [{"summary": "IT/data/cybersecurity executive."}]}


# --- Layer 1: deterministic exclusion --------------------------------------

@pytest.mark.parametrize("title", [
    "Registered Nurse - ICU",
    "Physician - Internal Medicine",
    "Firefighter/Paramedic",
    "Licensed Social Worker",
    "TITLE-5 Program Analyst",
    "IT Technician",
    "Summer Marketing Intern",
    "Volunteer Coordinator",
])
def test_deterministic_layer_excludes_unrelated_professions(title):
    skip = fit_score_prefilter.should_skip_scoring(_job(title), PROFILE)
    assert skip is not None
    assert skip["layer"] == "deterministic"


def test_deterministic_layer_does_not_flag_managing_director_as_medical():
    # Real near-miss found validating this against production data
    # (2026-08-11): a bare "MD" token collides with "Managing Director" in
    # finance titles ("Data and AI Chief Operating Officer, MD" scored 32
    # in real jobs.json) - the physician pattern must not use a bare MD
    # token, or this legitimately-scoring job would get silently skipped.
    skip = fit_score_prefilter._deterministic_exclude(_job("Data and AI Chief Operating Officer, MD"))
    assert skip is None


def test_deterministic_layer_skips_the_haiku_call_entirely(monkeypatch):
    calls = []
    monkeypatch.setattr(fit_score_prefilter, "_domain_plausibility", lambda job, profile: calls.append(1) or {"plausible": True})
    fit_score_prefilter.should_skip_scoring(_job("Registered Nurse"), PROFILE)
    assert calls == []  # never reached - deterministic match short-circuits


# --- Layer 2: domain plausibility (Haiku) -----------------------------------

def test_domain_layer_skips_a_wrong_field_role(monkeypatch):
    monkeypatch.setattr(
        fit_score_prefilter, "_domain_plausibility",
        lambda job, profile: {"plausible": False, "reason": "clinical leadership role, not IT/data"},
    )
    skip = fit_score_prefilter.should_skip_scoring(_job("Chief Nursing Officer"), PROFILE)
    assert skip == {"layer": "domain_check", "reason": "clinical leadership role, not IT/data"}


def test_domain_layer_lets_a_plausible_role_through(monkeypatch):
    monkeypatch.setattr(fit_score_prefilter, "_domain_plausibility", lambda job, profile: {"plausible": True, "reason": ""})
    skip = fit_score_prefilter.should_skip_scoring(_job("VP Information Technology"), PROFILE)
    assert skip is None


def test_domain_layer_defaults_to_plausible_when_the_model_omits_the_field(monkeypatch):
    # _domain_schema requires "plausible", but should_skip_scoring's own
    # .get(..., True) is the real safety net if a malformed/partial response
    # ever slipped through anyway - conservative default per the module's
    # own "false negative is the worse mistake" framing.
    monkeypatch.setattr(fit_score_prefilter, "_domain_plausibility", lambda job, profile: {"reason": "unclear"})
    skip = fit_score_prefilter.should_skip_scoring(_job("Director of Something Ambiguous"), PROFILE)
    assert skip is None


@pytest.mark.parametrize("exc_cls", [LLMNotConfigured, LLMCallFailed])
def test_domain_layer_fails_open_on_llm_error(monkeypatch, exc_cls):
    # Non-negotiable: a broken/unconfigured/rate-limited pre-filter call
    # must never itself block a real fit_score call.
    def _raise(job, profile):
        raise exc_cls("boom")
    monkeypatch.setattr(fit_score_prefilter, "_domain_plausibility", _raise)
    skip = fit_score_prefilter.should_skip_scoring(_job("VP Information Technology"), PROFILE)
    assert skip is None


def test_domain_plausibility_prompt_stays_minimal_not_full_profile_json(monkeypatch):
    # The whole point of this layer is staying far cheaper than the
    # fit_score call it's gating - assert the real call site never widens
    # into fit_score's own full-profile-JSON-plus-full-job-JSON shape.
    captured = {}

    def _fake_call_structured(client, system, user_content, schema, max_tokens, **kwargs):
        captured["user_content"] = user_content
        captured["max_tokens"] = max_tokens
        captured["model"] = kwargs.get("model")
        captured["effort"] = kwargs.get("effort")
        return {"plausible": True, "reason": ""}

    monkeypatch.setattr(fit_score_prefilter, "_client", lambda: object())
    monkeypatch.setattr(fit_score_prefilter, "call_structured", _fake_call_structured)
    fit_score_prefilter._domain_plausibility(_job("VP Information Technology", department="Corporate IT"), PROFILE)

    assert captured["model"] == fit_score_prefilter.PREFILTER_MODEL
    assert captured["max_tokens"] <= 300
    assert "work_history" not in captured["user_content"]
    assert "IT/data/cybersecurity executive." in captured["user_content"]
    assert "Corporate IT" in captured["user_content"]
    # Real bug found 2026-08-11: claude-haiku-4-5 rejects the effort key in
    # output_config outright ("This model does not support the effort
    # parameter") - every one of 126 real domain-check calls in that day's
    # live validation failed on this, silently caught by should_skip_
    # scoring's own fail-open handling. effort=None omits the key entirely
    # rather than guessing a value the model will accept.
    assert captured["effort"] is None


# --- Logging: the non-negotiable "never silently dropped" requirement ------

def test_log_prefilter_skip_appends_a_spot_check_record(isolated_data):
    job = _job("Registered Nurse", job_id="42", organization="Big Health System")
    fit_score_prefilter.log_prefilter_skip(job, {"layer": "deterministic", "reason": "clinical/medical practitioner role"})

    entries = read_json(fit_score_prefilter.PREFILTER_LOG_PATH, default=[])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "linkedin"
    assert entry["job_id"] == "42"
    assert entry["title"] == "Registered Nurse"
    assert entry["organization"] == "Big Health System"
    assert entry["layer"] == "deterministic"
    assert entry["reason"] == "clinical/medical practitioner role"
    assert "timestamp" in entry


def test_log_prefilter_skip_accumulates_across_multiple_jobs(isolated_data):
    fit_score_prefilter.log_prefilter_skip(_job("Registered Nurse", job_id="1"), {"layer": "deterministic", "reason": "a"})
    fit_score_prefilter.log_prefilter_skip(_job("Chief Nursing Officer", job_id="2"), {"layer": "domain_check", "reason": "b"})

    entries = read_json(fit_score_prefilter.PREFILTER_LOG_PATH, default=[])
    assert len(entries) == 2
    assert [e["job_id"] for e in entries] == ["1", "2"]
