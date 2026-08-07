"""User-managed allowlist of job-alert-digest senders (2026-08-07) -
scripts/job_alert_scan.py only scans mail from senders/domains configured
here, never a generic "looks like a job listing" heuristic (Zahir's
explicit ask, relayed via General: a defined allowlist avoids false
positives from ordinary personal mail). Same "user-editable from the
Settings tab, no code change needed" shape as job_sources.py - see that
module's docstring for the precedent this follows - but a different data
type (email senders, not ATS company identifiers), so its own file/store
rather than a shared one.

Each entry also carries which job_store `source` string newly-extracted
listings should be tagged with (e.g. "linkedin", "lensa") - matches the
existing MANUAL_JOB_SOURCE_OPTIONS convention in ui/app.py, so a listing
extracted from a configured sender groups with the rest of Panga's
per-channel data the same way a manually-pasted one does.

Plain YAML, not security/crypto_store.py - sender domains/addresses
aren't sensitive, same call as job_sources.yaml/industry_job_boards.yaml.
Locked with security/file_lock.py for the same reason job_sources.py is:
ui/app.py's Settings tab writes it from a live Streamlit session while
scripts/job_alert_scan.py could be mid-read."""

from pathlib import Path

import yaml

from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOB_ALERT_SENDERS_PATH = PROJECT_ROOT / "config" / "job_alert_senders.yaml"


def load_job_alert_senders() -> list[dict]:
    """Returns [{"sender": str, "source": str}, ...] - "sender" is a
    domain (e.g. "jobalerts-noreply@linkedin.com" or "lensa.com") matched
    against the From header, "source" is the job_store tag to save
    extracted listings under. Missing file resolves to an empty list, not
    an error - a fresh install has no senders configured yet, and
    job_alert_scan.py treats an empty list as "nothing to scan"."""
    with locked("job_alert_senders"):
        if not JOB_ALERT_SENDERS_PATH.exists():
            return []
        with open(JOB_ALERT_SENDERS_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    return data.get("senders") or []


def save_job_alert_senders(senders: list[dict]) -> None:
    """Overwrites the whole store. Callers should pass a full list (e.g.
    load_job_alert_senders(), mutate, save) rather than a partial one."""
    with locked("job_alert_senders"):
        JOB_ALERT_SENDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(JOB_ALERT_SENDERS_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump({"senders": senders}, f, sort_keys=False, allow_unicode=True)
