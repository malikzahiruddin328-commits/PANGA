"""Tests for google_calendar_client.py's pure logic: freebusy response
parsing, free-window computation, and the "believable subset" slot
curation heuristic. No live Calendar API call is made or needed for any
of these - the response-shape assumption in parse_freebusy_response is
tested against a fixed sample instead (the real endpoint wasn't reachable
to verify against from this environment, same as email_providers.py's
ISPDB tests and imap_client.py's protocol tests)."""

from datetime import datetime

from google_calendar_client import (
    compute_free_windows,
    curate_believable_slots,
    parse_freebusy_response,
)


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    # All test days land in the same August 2026 week for readability;
    # 2026-08-03 is a Monday.
    return datetime(2026, 8, day, hour, minute)


def test_parse_freebusy_response_extracts_busy_blocks():
    response = {
        "calendars": {
            "primary": {
                "busy": [
                    {"start": "2026-08-03T10:00:00", "end": "2026-08-03T11:00:00"},
                    {"start": "2026-08-03T14:00:00", "end": "2026-08-03T15:30:00"},
                ]
            }
        }
    }
    result = parse_freebusy_response(response)
    assert result == [
        (_dt(3, 10), _dt(3, 11)),
        (_dt(3, 14), _dt(3, 15, 30)),
    ]


def test_parse_freebusy_response_empty_calendar():
    assert parse_freebusy_response({"calendars": {"primary": {"busy": []}}}) == []


def test_compute_free_windows_fully_open_day():
    # Monday 2026-08-03, no busy blocks at all.
    windows = compute_free_windows(_dt(3, 0), _dt(3, 23), [])
    assert len(windows) == 1
    assert windows[0].start == _dt(3, 9)
    assert windows[0].end == _dt(3, 17)


def test_compute_free_windows_splits_around_a_busy_block():
    busy = [(_dt(3, 12), _dt(3, 13))]
    windows = compute_free_windows(_dt(3, 0), _dt(3, 23), busy)
    assert len(windows) == 2
    assert windows[0].start == _dt(3, 9) and windows[0].end == _dt(3, 12)
    assert windows[1].start == _dt(3, 13) and windows[1].end == _dt(3, 17)


def test_compute_free_windows_fully_busy_day_yields_nothing():
    busy = [(_dt(3, 8), _dt(3, 18))]
    windows = compute_free_windows(_dt(3, 0), _dt(3, 23), busy)
    assert windows == []


def test_compute_free_windows_skips_weekends():
    # 2026-08-08 is a Saturday, 2026-08-09 a Sunday.
    windows = compute_free_windows(_dt(8, 0), _dt(9, 23), [])
    assert windows == []


def test_compute_free_windows_drops_short_gaps():
    # A 10-minute gap shouldn't count as a usable slot at the default
    # 30-minute minimum.
    busy = [(_dt(3, 9), _dt(3, 9, 50)), (_dt(3, 10), _dt(3, 17))]
    windows = compute_free_windows(_dt(3, 0), _dt(3, 23), busy)
    assert windows == []


def test_curate_believable_slots_skips_first_day_when_choice_available():
    windows = [
        compute_free_windows(_dt(3, 0), _dt(3, 23), [])[0],  # Mon 3rd
        compute_free_windows(_dt(4, 0), _dt(4, 23), [])[0],  # Tue 4th
        compute_free_windows(_dt(5, 0), _dt(5, 23), [])[0],  # Wed 5th
        compute_free_windows(_dt(6, 0), _dt(6, 23), [])[0],  # Thu 6th
    ]
    picked = curate_believable_slots(windows, count=2)
    picked_days = [w.start.date() for w in picked]
    assert _dt(3, 0).date() not in picked_days  # earliest day skipped
    assert len(picked) == 2


def test_curate_believable_slots_returns_all_when_scarce():
    windows = [compute_free_windows(_dt(3, 0), _dt(3, 23), [])[0]]
    picked = curate_believable_slots(windows, count=3)
    assert len(picked) == 1  # only one day free - nothing to skip, don't reduce further


def test_curate_believable_slots_trims_to_slot_duration():
    windows = compute_free_windows(_dt(3, 0), _dt(3, 23), [])  # 9am-5pm, one big window
    picked = curate_believable_slots(windows, count=1, slot_duration_minutes=30)
    assert picked[0].duration_minutes() == 30
    assert picked[0].start == _dt(3, 9)


def test_curate_believable_slots_one_slot_per_day():
    # Two separate free windows on the same day (a mid-day meeting split
    # them) should still only ever contribute one offered slot for that day.
    busy = [(_dt(3, 12), _dt(3, 13))]
    windows = compute_free_windows(_dt(3, 0), _dt(3, 23), busy)
    assert len(windows) == 2  # sanity: both windows exist
    picked = curate_believable_slots(windows, count=5)
    assert len(picked) == 1


def test_using_gmail_credentials_true_when_only_gmail_present(tmp_path, monkeypatch):
    import google_calendar_client as gcc

    monkeypatch.setattr(gcc, "CREDENTIALS_PATH", tmp_path / "calendar_creds.json")
    monkeypatch.setattr(gcc, "GMAIL_CREDENTIALS_PATH", tmp_path / "gmail_creds.json")
    (tmp_path / "gmail_creds.json").write_text("{}")
    assert gcc.using_gmail_credentials() is True


def test_using_gmail_credentials_false_when_own_creds_present(tmp_path, monkeypatch):
    import google_calendar_client as gcc

    monkeypatch.setattr(gcc, "CREDENTIALS_PATH", tmp_path / "calendar_creds.json")
    monkeypatch.setattr(gcc, "GMAIL_CREDENTIALS_PATH", tmp_path / "gmail_creds.json")
    (tmp_path / "calendar_creds.json").write_text("{}")
    (tmp_path / "gmail_creds.json").write_text("{}")
    assert gcc.using_gmail_credentials() is False
