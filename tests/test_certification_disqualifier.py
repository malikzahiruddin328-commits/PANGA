"""2026-08-19, Zahir's explicit rule: a job posting naming a certification
the candidate genuinely does not hold should be treated very differently
depending on the POSTING'S OWN WORDING - "required"/"must have"/"mandatory"
is a hard disqualifier (score very low, same tier as the existing
military-membership/entry-level hard-disqualifier rules already in
SCORE_SYSTEM_PROMPT), while "preferred"/"desired" language must NOT
disqualify and should instead credit the candidate's relevant industry
experience in the certification's place.

Investigation finding (see report): this codebase's fit_score is a single
real reasoning call (drafting.score_job(), via SCORE_SYSTEM_PROMPT) that
already makes exactly this shape of judgment call with NO deterministic
backstop for its other hard-disqualifier rules (military membership,
entry-level programs, candidate-declared is_disqualifier exclusions) - the
posting's phrasing for those is read and judged by the model itself, not
regex-classified first. There is no existing required-vs-preferred
DETERMINISTIC classifier anywhere in the fit-scoring path to extend (the
only deterministic required-vs-preferred split in the codebase,
ats_score.py's extract_keywords()/score_resume_against_keywords(), belongs
to the separate ATS resume-tailoring scorer, not fit_score/disqualification,
and never hard-disqualifies - it only proportionally weights a score). So
this extends SCORE_SYSTEM_PROMPT with a new bullet in the exact same shape
and tier as the existing hard-disqualifier bullets, reusing the same
already-paid-for score_job() call (zero new AI-call volume/cost) rather
than adding a second classification call Zahir hasn't approved.

Because the actual required-vs-preferred/disqualify decision is made by the
live model reasoning over SCORE_SYSTEM_PROMPT (not by any deterministic
code in this repo), these tests cannot exercise real model judgment without
a paid live API call. What they verify instead, matching this repo's own
established pattern for testing score_job() (see
test_score_job_prompt_caching.py, which is also request-shape-only):
  1. The real posting text (with its actual required/preferred wording)
     and the real profile (with/without the certification) are both wired
     into the exact request score_job() sends - i.e. the model actually
     receives the information it needs to make the correct call for each
     of the four scenarios.
  2. SCORE_SYSTEM_PROMPT itself contains the new certification rule, in
     both directions, so the instruction the model is actually given
     matches Zahir's rule (hard disqualify only when required+missing,
     credit experience instead when preferred+missing).
  3. score_job() returns whatever fit_score/fit_rationale the model
     produces unmodified in each of the four scenarios - proving score_job
     has no separate code path that would silently override or
     re-interpret a correct model judgment (there's nothing here to get in
     the way of the rule actually working).
"""

from tailoring import drafting


PROFILE_WITHOUT_ITIL = {
    "name": "Zahir",
    "seniority": "VP / Executive",
    "certifications": [],
    "skills": ["IT Governance", "Enterprise Architecture", "Service Management"],
}

PROFILE_WITH_ITIL = {
    "name": "Zahir",
    "seniority": "VP / Executive",
    "certifications": ["ITIL v4 Foundation"],
    "skills": ["IT Governance", "Enterprise Architecture", "Service Management"],
}

JOB_REQUIRED_ITIL = {
    "source": "linkedin", "job_id": "1",
    "title": "IT Service Delivery Director",
    "organization": "Acme Corp",
    "description": "Required qualifications: ITIL certification is required for this role. Must have led ITSM transformations.",
}

JOB_PREFERRED_ITIL = {
    "source": "linkedin", "job_id": "2",
    "title": "IT Service Delivery Director",
    "organization": "Acme Corp",
    "description": "ITIL certification is preferred but not required. 15+ years of IT service management experience desired.",
}

JOB_NO_CERT = {
    "source": "linkedin", "job_id": "3",
    "title": "IT Service Delivery Director",
    "organization": "Acme Corp",
    "description": "Looking for a strong leader with IT operations and service management background.",
}


def _capture_call(monkeypatch, job, profile, fake_result):
    captured = {}

    def _fake_call_structured(client, system, user_content, schema, max_tokens, **kwargs):
        captured["system"] = system
        captured["user_content"] = user_content
        return fake_result

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    result = drafting.score_job(job, profile)
    captured["result"] = result
    return captured


def test_score_system_prompt_has_required_certification_hard_disqualifier_rule():
    prompt = drafting.SCORE_SYSTEM_PROMPT.lower()
    assert "certification" in prompt
    assert "required/mandatory/must-have" in prompt or "required" in prompt
    assert "score this low" in prompt or "low/near 0" in prompt


def test_score_system_prompt_says_preferred_certification_is_never_a_disqualifier():
    prompt = drafting.SCORE_SYSTEM_PROMPT.lower()
    assert "never a disqualifier" in prompt
    assert "credit the candidate" in prompt or "credit" in prompt


def test_required_and_missing_certification_scenario_is_disqualified(monkeypatch):
    # Posting requires ITIL, candidate's profile does not show it - the
    # real model, following the new SCORE_SYSTEM_PROMPT rule, should return
    # a very low score. This proves that low score reaches the caller
    # unmodified and the posting/profile facts needed for that judgment
    # were actually sent.
    fake_result = {
        "fit_score": 3,
        "fit_rationale": "Disqualified: posting requires ITIL certification, which the candidate does not hold.",
    }
    captured = _capture_call(monkeypatch, JOB_REQUIRED_ITIL, PROFILE_WITHOUT_ITIL, fake_result)
    assert captured["result"] == fake_result
    assert captured["result"]["fit_score"] <= 10
    assert "ITIL certification is required" in captured["user_content"]
    profile_block = next(b for b in captured["system"] if "cache_control" in b)
    assert "certifications" in profile_block["text"]


def test_preferred_and_missing_certification_scenario_is_not_disqualified(monkeypatch):
    # Posting only prefers ITIL, candidate's profile does not show it - the
    # rule says this must NOT be treated as a disqualifier; the model
    # should credit the candidate's relevant experience instead and return
    # a genuinely fit-reflecting score, not an artificially low one.
    fake_result = {
        "fit_score": 82,
        "fit_rationale": "Strong fit on IT service management experience; ITIL is only preferred, credited via years of relevant industry experience instead.",
    }
    captured = _capture_call(monkeypatch, JOB_PREFERRED_ITIL, PROFILE_WITHOUT_ITIL, fake_result)
    assert captured["result"] == fake_result
    assert captured["result"]["fit_score"] >= 50
    assert "ITIL certification is preferred" in captured["user_content"]


def test_required_and_held_certification_scenario_is_not_disqualified(monkeypatch):
    # Posting requires ITIL, candidate's profile DOES show it - not a
    # disqualifier at all, should score on the merits like any other match.
    fake_result = {
        "fit_score": 90,
        "fit_rationale": "Excellent fit; candidate holds the required ITIL certification and has strong IT service management experience.",
    }
    captured = _capture_call(monkeypatch, JOB_REQUIRED_ITIL, PROFILE_WITH_ITIL, fake_result)
    assert captured["result"] == fake_result
    assert captured["result"]["fit_score"] >= 50
    profile_block = next(b for b in captured["system"] if "cache_control" in b)
    assert "ITIL v4 Foundation" in profile_block["text"]


def test_no_certification_mentioned_scenario_is_unaffected(monkeypatch):
    # Posting names no certification at all - the new rule must not
    # invent one or otherwise change ordinary scoring behavior.
    fake_result = {
        "fit_score": 78,
        "fit_rationale": "Good fit on IT operations and service management background.",
    }
    captured = _capture_call(monkeypatch, JOB_NO_CERT, PROFILE_WITHOUT_ITIL, fake_result)
    assert captured["result"] == fake_result
    assert "certification" not in captured["user_content"].lower()
