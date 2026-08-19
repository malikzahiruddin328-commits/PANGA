"""Independent kill switch for the paid fit_score scoring pipeline
(2026-08-18, Zahir's explicit ask).

Real gap this closes: before this, the ONLY thing stopping
scripts.run_search.score_unscored_jobs() from running was that the
panga-daily-job-search scheduled task happened to be disabled - the
function itself had no awareness that scoring was supposed to be paused,
so a direct script run (or the scheduled task being re-enabled by
accident) could spend real money with no warning. Real incident this
mirrors: a direct script run spent $3.14 on fit_score the same night
scoring was believed to be "on ice" purely because the scheduled task was
off.

review_status == "accepted" and "eligible for scoring" are now two
separate concepts on purpose - see job_store.py's add_manual_job() for
the review-gate side of this. A job can be accepted (auto or manual)
without ever being scored while this flag is on, and this flag has no
opinion about how a job became accepted.

Settings-controlled (config/settings.yaml's scoring_paused key, default
True right now since scoring genuinely is on ice per Zahir - see
memory project_panga_cost_reduction_target) so turning it back on is a
deliberate, visible Settings action, not a code change."""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def is_scoring_paused() -> bool:
    """True unless Settings explicitly sets scoring_paused: false. Missing
    key/file defaults to paused (fail-safe: an unconfigured or corrupted
    settings file should never silently let real spend through)."""
    if not SETTINGS_PATH.exists():
        return True
    try:
        settings = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return True
    return bool(settings.get("scoring_paused", True))
