from datetime import datetime, timedelta, timezone

from prospector.kpis import activity_summary, coverage_summary, outcome_summary

NOW = datetime.now(timezone.utc)


def iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def test_coverage_summary_counts_by_channel_and_recency():
    jobs = [
        {"source": "USAJOBS", "date_added": iso(1)},
        {"source": "USAJOBS", "date_added": iso(10)},
        {"source": "Dice", "date_added": None},
    ]
    result = coverage_summary(jobs)
    assert result["total_jobs"] == 3
    assert result["by_channel"] == {"USAJOBS": 2, "Dice": 1}
    assert result["added_last_7_days"] == 1
    assert result["untimestamped"] == 1


def test_activity_summary_counts_by_status():
    apps = [{"status": "applied", "created_at": iso(1)}, {"status": "applied"}, {"status": "rejected"}]
    result = activity_summary(apps)
    assert result["total_applications"] == 3
    assert result["by_status"] == {"applied": 2, "rejected": 1}
    assert result["created_last_7_days"] == 1
    assert result["untimestamped"] == 2


def test_outcome_summary_only_counts_applied_or_later():
    apps = [
        {"source": "Dice", "job_id": "1", "status": "under review"},
        {"source": "Dice", "job_id": "2", "status": "applied"},
        {"source": "Dice", "job_id": "3", "status": "rejected"},
        {"source": "Dice", "job_id": "4", "status": "offer"},
    ]
    result = outcome_summary(apps, jobs=[])
    assert result["overall"]["applied"] == 3
    assert result["overall"]["offer_rate"] == round(1 / 3, 2)
    assert result["overall"]["rejection_rate"] == round(1 / 3, 2)


def test_outcome_summary_current_status_only_not_double_counted():
    # An application that passed through "interview scheduled" before
    # landing on "offer" is stored with status "offer" only - outcome_summary
    # has no history, so it must count under offer alone, not both.
    apps = [{"source": "Dice", "job_id": "1", "status": "offer"}]
    result = outcome_summary(apps, jobs=[])
    assert result["overall"]["offer_rate"] == 1.0
    assert result["overall"]["interview_rate"] == 0.0


def test_outcome_summary_empty_applied_set_has_none_rates():
    result = outcome_summary([{"source": "Dice", "job_id": "1", "status": "under review"}], jobs=[])
    assert result["overall"]["applied"] == 0
    assert result["overall"]["response_rate"] is None


def test_outcome_summary_slices_by_score_band():
    jobs = [{"source": "Dice", "job_id": "1", "fit_score": 95}]
    apps = [{"source": "Dice", "job_id": "1", "status": "applied"}]
    result = outcome_summary(apps, jobs)
    assert "90-100" in result["by_score_band"]
    assert result["by_score_band"]["90-100"]["applied"] == 1
