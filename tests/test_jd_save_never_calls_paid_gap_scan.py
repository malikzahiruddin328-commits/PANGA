"""Real cost incident (2026-08-19, live production, Zahir watching it
happen): render_paste_jd_prompt_before_drafting() auto-fired
_analyze_fit_with_auto_gap_scan() the instant a JD was saved, which itself
unconditionally calls request_additional_gap_questions() - a real PAID
Anthropic API call - with no explicit action from the user beyond pasting
and saving text. "if i am adding a jd which is simple copy and paste...
shold be just save." Confirmed in today's real cost_log.json:
answer_more_gap_questions billed $1.78 across 5 calls from exactly this
session's JD-paste testing.

Fix: the proactive paste-JD-before-drafting flow now uses the plain
deterministic analyze_fit_before_drafting() (local keyword matching, no
network call) instead of the auto-gap-scan wrapper. The paid free-form
gap scan is still available - just opt-in via the existing manual "Answer
more questions" button, never fired automatically by a save."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score

APP_PATH = "src/ui/app.py"


@pytest.fixture
def results_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Indeed", "job_id": "nojd1", "title": "Director, Never Drafted",
        "organization": "Acme Corp", "location": "Remote",
    }])
    update_job_score("Indeed", "nojd1", 85, "Strong match.")
    return AppTest.from_file(APP_PATH)


def test_saving_a_jd_never_calls_the_paid_gap_scan(results_app, monkeypatch):
    import tailoring.drafting as drafting

    calls = []
    monkeypatch.setattr(drafting, "request_additional_gap_questions", lambda *a, **k: calls.append(1) or {"added_count": 0, "new_questions": [], "merged_clarifying_questions": []})

    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    paste_box = next(t for t in at.text_area if t.key.startswith("jd_paste_pre_Indeed_nojd1"))
    paste_box.set_value("Requirements: Python, AWS.")
    save_button = next(b for b in at.button if b.key == "jd_paste_pre_save_Indeed_nojd1")
    save_button.click().run(timeout=30)

    assert not at.exception
    assert calls == [], "Saving a JD must never trigger the paid free-form gap scan - it's opt-in only, via 'Answer more questions'."


def test_saving_a_jd_still_shows_a_real_score_card(results_app):
    # The fix must not silently drop the score card itself (Zahir's
    # earlier explicit ask, 2026-08-09) - only the auto-fired PAID call
    # is removed. The deterministic (free) score still renders.
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    paste_box = next(t for t in at.text_area if t.key.startswith("jd_paste_pre_Indeed_nojd1"))
    paste_box.set_value("Requirements: Python, AWS.")
    save_button = next(b for b in at.button if b.key == "jd_paste_pre_save_Indeed_nojd1")
    save_button.click().run(timeout=30)

    assert not at.exception
    assert any(m.label == "Projected score" for m in at.metric)
