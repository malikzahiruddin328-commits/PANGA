import cost_log
from cost_log import last_cost_for_job, load_cost_log, log_api_cost


def test_log_api_cost_appends_a_real_entry(isolated_data):
    log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1000, output_tokens=200, cost_usd=0.01)
    entries = load_cost_log()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["purpose"] == "fit_score"
    assert entry["model"] == "claude-opus-5"
    assert entry["input_tokens"] == 1000
    assert entry["output_tokens"] == 200
    assert entry["cost_usd"] == 0.01
    assert "timestamp" in entry
    assert "source" not in entry
    assert "job_id" not in entry


def test_log_api_cost_stamps_duration_ms_when_given(isolated_data):
    log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1000, output_tokens=200, cost_usd=0.01, duration_ms=1234.5)
    entry = load_cost_log()[0]
    assert entry["duration_ms"] == 1234.5


def test_log_api_cost_duration_ms_defaults_to_none(isolated_data):
    log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1000, output_tokens=200, cost_usd=0.01)
    entry = load_cost_log()[0]
    assert entry["duration_ms"] is None


def test_log_api_cost_stamps_job_key_when_given(isolated_data):
    log_api_cost(
        purpose="draft_resume", model="claude-opus-5", input_tokens=5000, output_tokens=3000, cost_usd=0.1,
        job_key=("linkedin", "12345"),
    )
    entry = load_cost_log()[0]
    assert entry["source"] == "linkedin"
    assert entry["job_id"] == "12345"


def test_log_api_cost_appends_without_clobbering_prior_entries(isolated_data):
    log_api_cost(purpose="a", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.001)
    log_api_cost(purpose="b", model="claude-opus-5", input_tokens=2, output_tokens=2, cost_usd=0.002)
    entries = load_cost_log()
    assert len(entries) == 2
    assert [e["purpose"] for e in entries] == ["a", "b"]


def test_last_cost_for_job_returns_the_most_recent_matching_entry(isolated_data, monkeypatch):
    # Stamp timestamps explicitly rather than relying on real wall-clock
    # ordering across fast successive calls in a test.
    entries = [
        {"timestamp": "2026-08-01T00:00:00+00:00", "source": "linkedin", "job_id": "1", "purpose": "draft_resume", "cost_usd": 0.05},
        {"timestamp": "2026-08-05T00:00:00+00:00", "source": "linkedin", "job_id": "1", "purpose": "draft_resume", "cost_usd": 0.09},
        {"timestamp": "2026-08-03T00:00:00+00:00", "source": "linkedin", "job_id": "2", "purpose": "draft_resume", "cost_usd": 0.20},
    ]
    from security.crypto_store import write_json
    write_json(cost_log.COST_LOG_PATH, entries)

    assert last_cost_for_job("linkedin", "1") == 0.09
    assert last_cost_for_job("linkedin", "2") == 0.20


def test_last_cost_for_job_returns_none_when_nothing_logged(isolated_data):
    assert last_cost_for_job("linkedin", "does-not-exist") is None


def test_last_cost_for_job_can_narrow_to_a_specific_purpose(isolated_data):
    log_api_cost(purpose="ats_keyword_extraction", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.001, job_key=("linkedin", "1"))
    log_api_cost(purpose="draft_resume", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.05, job_key=("linkedin", "1"))
    assert last_cost_for_job("linkedin", "1", purpose="draft_resume") == 0.05
    assert last_cost_for_job("linkedin", "1", purpose="ats_keyword_extraction") == 0.001


# 2026-08-10 audit fix #26: failed calls are now loggable too, with their
# own diagnostic fields, so real failed-call volume is no longer invisible.

def test_log_api_cost_defaults_success_to_true(isolated_data):
    log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1000, output_tokens=200, cost_usd=0.01)
    entry = load_cost_log()[0]
    assert entry["success"] is True


def test_log_api_cost_records_a_failed_call(isolated_data):
    log_api_cost(
        purpose="fit_score", model="claude-opus-5", input_tokens=0, output_tokens=0, cost_usd=0.0,
        duration_ms=3200.0, success=False, error_type="overloaded_error",
        attempt_count=4, models_tried=["claude-opus-5", "claude-opus-5", "claude-opus-5", "claude-sonnet-5"],
    )
    entry = load_cost_log()[0]
    assert entry["success"] is False
    assert entry["error_type"] == "overloaded_error"
    assert entry["attempt_count"] == 4
    assert entry["models_tried"] == ["claude-opus-5", "claude-opus-5", "claude-opus-5", "claude-sonnet-5"]
    assert entry["cost_usd"] == 0.0


def test_log_api_cost_omits_failure_fields_on_success(isolated_data):
    # A success entry shouldn't carry error_type/attempt_count/models_tried
    # at all - keeps old entries and new success entries shaped the same.
    log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1000, output_tokens=200, cost_usd=0.01)
    entry = load_cost_log()[0]
    assert "error_type" not in entry
    assert "attempt_count" not in entry
    assert "models_tried" not in entry
