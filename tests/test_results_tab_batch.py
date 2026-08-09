"""Covers the 2026-08-06 Results-tab batch (relayed via hub):

1. Channel expanders now carry an explicit, stable key (investigated a
   reported "USAJOBS starts expanded, Indeed doesn't" inconsistency -
   couldn't reproduce it in the code itself, since every channel already
   got a flat expanded=False with no per-channel branching; added the key
   as hardening regardless, and it's worth a real assertion that every
   channel's initial expanded state is False, no exceptions).
2. Inline "Pass" action: a ButtonColumn on the results table (can't be
   click-simulated here - AppTest's Dataframe element only exposes a
   static .value snapshot, no interaction API for button columns, the same
   real limitation the pre-existing Role column's own comment already
   documents) opens a categorized-reason st.dialog. Tested here by driving
   the dialog directly via session_state (exactly what the ButtonColumn's
   on_click callback would set on a real click) - covers the dialog's own
   logic (category selection, free-text branch, confirm/cancel) fully,
   just not the literal canvas click that triggers it.
3. Results tab's nav-bar badge shows the filtered count (matching the
   page's own "N job(s)" heading) instead of the raw job-store total, with
   the scanned total kept visible in a caption instead of dropped.
"""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application, get_application

APP_PATH = "src/ui/app.py"


@pytest.fixture
def results_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")

    save_jobs([
        {"source": "USAJOBS", "job_id": "u1", "title": "Program Analyst", "organization": "DoD", "location": "Remote"},
        {"source": "Indeed", "job_id": "i1", "title": "Data Engineer", "organization": "Acme Corp", "location": "Remote"},
        {"source": "Dice", "job_id": "d1", "title": "Platform Engineer", "organization": "Beta Inc", "location": "Remote"},
    ])
    update_job_score("USAJOBS", "u1", 85, "Strong match.")
    update_job_score("Indeed", "i1", 80, "Good match.")
    update_job_score("Dice", "d1", 40, "Below threshold.")
    return AppTest.from_file(APP_PATH)


def test_every_channel_expander_starts_collapsed(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    assert not at.exception
    # Match by label prefix, not key - Expandable's proto has no key field
    # of its own to filter on (same reason the pre-existing JD-warning
    # tests match View/update job description's expander by label too).
    channel_expanders = [e for e in at.expander if e.label.startswith("USAJOBS") or e.label.startswith("Indeed")]
    # Both channels with a scored, above-threshold job should have rendered
    # a section (USAJOBS and Indeed - Dice's job is below the default 70
    # threshold and won't have its own section).
    assert len(channel_expanders) >= 2
    assert all(not e.proto.expanded for e in channel_expanders)


def test_pass_column_present_with_constant_label(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    assert not at.exception
    found_pass_column = False
    for d in at.dataframe:
        df = d.value
        if "Pass" in df.columns:
            found_pass_column = True
            assert all(v == "Pass" for v in df["Pass"])
    assert found_pass_column


def test_results_badge_shows_filtered_count_not_raw_total(results_app):
    at = results_app
    at.run(timeout=30)

    assert not at.exception
    # 3 jobs scanned total; only USAJOBS (85) and Indeed (80) clear the
    # default 70-point threshold - Dice (40) doesn't, so the badge should
    # read 2, never the raw job-store total of 3... but since 3 is small
    # enough to look plausible either way, assert the actual mechanism
    # instead: the badge and the page's own live heading must agree.
    tab_buttons = [b for b in at.button if (b.proto.help or "") == "" and "Results (" in (b.label or "")]
    assert tab_buttons, "no Results tab button found"
    badge_label = tab_buttons[0].label

    at.session_state["active_tab"] = "results"
    at.run(timeout=30)
    headings = [m.value for m in at.subheader if m.value.endswith("job(s)")]
    assert headings, "no 'N job(s)' heading found on the Results tab"
    live_count = headings[0].split(" ")[0]
    assert live_count == "2"
    assert badge_label == f"Results ({live_count})"


def test_results_caption_keeps_the_scanned_total_visible(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    assert not at.exception
    # st.markdown, not st.caption - this project's standing readability
    # rule (Mirror's fine-needle audit caught this exact call, added in
    # the batch just before this one, as a straggler, 2026-08-06).
    scanned_lines = [m.value for m in at.markdown if "scanned" in m.value]
    assert scanned_lines
    assert "3 job(s) scanned" in scanned_lines[0]
    assert "2 match" in scanned_lines[0]


def test_pass_dialog_shows_categorized_reasons_not_open_text(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["pass_dialog_pending"] = {"source": "USAJOBS", "job_id": "u1", "label": "Program Analyst - DoD"}
    at.run(timeout=30)

    assert not at.exception
    radios = [r for r in at.radio if r.key == "pass_reason_category"]
    assert len(radios) == 1
    assert radios[0].options == [
        "Wrong seniority level", "Not my industry/domain", "Comp too low",
        "Location doesn't work", "Something else",
    ]
    # No free-text box until "Something else" is actually picked.
    assert not any(t.key == "pass_reason_free_text" for t in at.text_area)


def test_confirming_a_categorized_reason_sets_status_and_skip_reason(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["pass_dialog_pending"] = {"source": "USAJOBS", "job_id": "u1", "label": "Program Analyst - DoD"}
    at.run(timeout=30)

    radio = next(r for r in at.radio if r.key == "pass_reason_category")
    radio.set_value("Comp too low")
    confirm = next(b for b in at.button if b.key == "pass_reason_confirm")
    confirm.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("USAJOBS", "u1")
    assert app_record["status"] == "not interested"
    assert app_record["skip_reason"] == "Comp too low"
    # Reusing applications.py's existing review loop, not a parallel one.
    assert app_record["skip_reason_reviewed"] is False
    assert "pass_dialog_pending" not in at.session_state


def test_confirming_something_else_keeps_both_the_category_and_the_free_text(results_app):
    # Regression test for a real bug (Zahir's original ask, weeks earlier,
    # never actually built - caught live-testing again 2026-08-07): the
    # confirm handler used to store ONLY the typed free text, silently
    # discarding "Something else" so future data-profiling could no longer
    # tell "which bucket" from "what he actually said".
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["pass_dialog_pending"] = {"source": "Indeed", "job_id": "i1", "label": "Data Engineer - Acme Corp"}
    at.run(timeout=30)

    radio = next(r for r in at.radio if r.key == "pass_reason_category")
    radio.set_value("Something else")
    at.run(timeout=30)

    free_text = next(t for t in at.text_area if t.key == "pass_reason_free_text")
    free_text.set_value("Hiring manager gave off bad vibes on the phone screen.")
    confirm = next(b for b in at.button if b.key == "pass_reason_confirm")
    confirm.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Indeed", "i1")
    assert app_record["status"] == "not interested"
    assert app_record["skip_reason"] == "Something else\nHiring manager gave off bad vibes on the phone screen."
    category, detail = app_record["skip_reason"].split("\n", 1)
    assert category == "Something else"
    assert detail == "Hiring manager gave off bad vibes on the phone screen."

    # upsert_application() regenerates the dossier as a real side effect
    # (applications.py's _write_dossier calls) - closes the loop end to end,
    # driven by the same widget interactions a real click would produce,
    # not just an assertion on the in-memory application record.
    from tailoring.dossier import dossier_path
    path = dossier_path("Indeed", "i1", "Acme Corp", "Data Engineer")
    dossier_lines = path.read_text(encoding="utf-8").splitlines()
    assert "- **Skip reason:** Something else" in dossier_lines
    assert "  - Hiring manager gave off bad vibes on the phone screen." in dossier_lines


def test_confirming_something_else_with_blank_free_text_stores_just_the_category(results_app):
    # If Zahir picks "Something else" but leaves the box blank, there's no
    # detail to preserve - falls back to the plain category string, same
    # as every other category.
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["pass_dialog_pending"] = {"source": "Dice", "job_id": "d1", "label": "Platform Engineer - Beta Inc"}
    at.run(timeout=30)

    radio = next(r for r in at.radio if r.key == "pass_reason_category")
    radio.set_value("Something else")
    confirm = next(b for b in at.button if b.key == "pass_reason_confirm")
    confirm.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "d1")
    assert app_record["skip_reason"] == "Something else"


def test_cancel_closes_the_dialog_without_changing_status(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["pass_dialog_pending"] = {"source": "USAJOBS", "job_id": "u1", "label": "Program Analyst - DoD"}
    at.run(timeout=30)

    cancel = next(b for b in at.button if b.key == "pass_reason_cancel")
    cancel.click().run(timeout=30)

    assert not at.exception
    assert get_application("USAJOBS", "u1") is None
    assert "pass_dialog_pending" not in at.session_state


def test_no_automatic_filtering_based_on_skip_reason(results_app):
    # Explicitly confirmed with Zahir (relayed via hub): store reasons for
    # later pattern analysis only, never auto-hide/suppress based on them.
    # A "not interested" job is hidden by the EXISTING show_not_interested
    # checkbox mechanism (already tested elsewhere) - nothing about the
    # reason category itself should add any additional suppression logic.
    at = results_app
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    import inspect
    import ui.app as app_module

    source = inspect.getsource(app_module.compute_results_ranked)
    assert "skip_reason" not in source
