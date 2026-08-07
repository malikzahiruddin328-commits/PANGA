"""Tests for src/search/job_alert_senders.py's load/save round-trip -
same shape as job_sources.py, which this module was modeled on."""

from pathlib import Path

import search.job_alert_senders as job_alert_senders


def test_missing_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(job_alert_senders, "JOB_ALERT_SENDERS_PATH", tmp_path / "job_alert_senders.yaml")
    assert job_alert_senders.load_job_alert_senders() == []


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(job_alert_senders, "JOB_ALERT_SENDERS_PATH", tmp_path / "job_alert_senders.yaml")
    senders = [
        {"sender": "jobalerts-noreply@linkedin.com", "source": "linkedin"},
        {"sender": "lensa.com", "source": "lensa"},
    ]
    job_alert_senders.save_job_alert_senders(senders)
    assert job_alert_senders.load_job_alert_senders() == senders


def test_save_creates_parent_directory(tmp_path, monkeypatch):
    nested_path = tmp_path / "nested" / "job_alert_senders.yaml"
    monkeypatch.setattr(job_alert_senders, "JOB_ALERT_SENDERS_PATH", nested_path)
    job_alert_senders.save_job_alert_senders([{"sender": "lensa.com", "source": "lensa"}])
    assert nested_path.exists()


def test_save_overwrites_rather_than_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(job_alert_senders, "JOB_ALERT_SENDERS_PATH", tmp_path / "job_alert_senders.yaml")
    job_alert_senders.save_job_alert_senders([{"sender": "lensa.com", "source": "lensa"}])
    job_alert_senders.save_job_alert_senders([{"sender": "linkedin.com", "source": "linkedin"}])
    assert job_alert_senders.load_job_alert_senders() == [{"sender": "linkedin.com", "source": "linkedin"}]
