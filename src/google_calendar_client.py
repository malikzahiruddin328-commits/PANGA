"""Google Calendar client for calendar-aware CTA interview-request replies
(native-packaging, see docs/multi-provider-accounts-scope.md Part B).
Google Calendar only for v1 - Outlook/Apple calendars are explicit
follow-ons, not built here (same "Gmail first" precedent as Part A).

OAuth model: identical shape to gmail_client.py, and deliberately able to
reuse the SAME Google Cloud OAuth client if Zahir already set one up for
Gmail (an OAuth "Desktop app" client isn't tied to one specific Google
API - get_credentials() falls back to gmail_client's credentials.json if
this module's own data/google_calendar/credentials.json doesn't exist, so
Calendar access can piggyback Gmail's existing one-time setup rather than
requiring a second Google Cloud Console trip). Token storage stays
separate (data/google_calendar/token.json) since the granted scope
differs and the two should be revocable independently.

Only the read-only freebusy scope is requested - this module never reads
event titles/details and never writes to the calendar at all; it only
ever needs to know when the calendar is BLOCKED, not what's on it (see the
scope doc's explicit "no calendar write access" boundary and the privacy
reasoning below).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from security.crypto_store import read_text, write_text
from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS_PATH = PROJECT_ROOT / "data" / "google_calendar" / "credentials.json"
GMAIL_CREDENTIALS_PATH = PROJECT_ROOT / "data" / "gmail" / "credentials.json"
TOKEN_PATH = PROJECT_ROOT / "data" / "google_calendar" / "token.json"

# freebusy-only - deliberately narrower than a general calendar.readonly
# scope, since this module never needs to know what's ON the calendar,
# only when it's blocked (see module docstring).
SCOPES = ["https://www.googleapis.com/auth/calendar.freebusy"]


class GoogleCalendarNotConfigured(Exception):
    pass


def _credentials_source_path() -> Optional[Path]:
    if CREDENTIALS_PATH.exists():
        return CREDENTIALS_PATH
    if GMAIL_CREDENTIALS_PATH.exists():
        return GMAIL_CREDENTIALS_PATH
    return None


def is_configured() -> bool:
    return _credentials_source_path() is not None


def using_gmail_credentials() -> bool:
    """True if this module is riding on Gmail's existing OAuth client
    rather than a separate one of its own - the Settings UI uses this to
    tell the user their Gmail sign-in already covers Calendar too."""
    return not CREDENTIALS_PATH.exists() and GMAIL_CREDENTIALS_PATH.exists()


def _load_cached_credentials() -> Optional[Credentials]:
    token_json = read_text(TOKEN_PATH, default=None)
    if token_json is None:
        return None
    import json
    return Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)


def _save_credentials(creds: Credentials) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_text(TOKEN_PATH, creds.to_json())


def get_credentials() -> Credentials:
    """Same refresh-or-consent shape as gmail_client.py's get_credentials.
    Raises GoogleCalendarNotConfigured if neither this module's own OAuth
    client nor Gmail's existing one is available.

    The load-check-refresh-save sequence runs under a lock (2026-08-08,
    Mirror's audit flagged this module as missing the same fix
    gmail_client.py/microsoft_client.py already got in commit 81259f8 -
    Calendar is reachable from the same shared fulfillment path
    (interview-reply calendar-slot lookups) that motivated the original
    fix, so it needed the identical treatment, just missed at the time).
    Locking around the network refresh call itself (not just the file
    write) is deliberate: it serializes concurrent refresh attempts, not
    just concurrent writes - same reasoning as gmail_client.py's
    get_credentials."""
    source = _credentials_source_path()
    if source is None:
        raise GoogleCalendarNotConfigured(
            "No Google OAuth client found for Calendar access. If Gmail is "
            "already set up, this should have reused its client - check "
            f"{GMAIL_CREDENTIALS_PATH}. Otherwise, in Google Cloud Console: "
            "enable the Calendar API, create an OAuth client (type "
            f"'Desktop app'), download its JSON, and save it as {CREDENTIALS_PATH}."
        )

    with locked("google_calendar_token"):
        creds = _load_cached_credentials()
        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_credentials(creds)
                return creds
            except RefreshError:
                creds = None  # refresh token itself revoked/expired - fall through to re-consent

    # No valid or refreshable cached token - the one-time interactive
    # consent flow, deliberately OUTSIDE the lock above: this needs a real
    # person clicking through a browser window, which can take far longer
    # than the lock's own wait bound, and two processes both hitting
    # first-time consent at once doesn't corrupt anything even unlocked -
    # each just uses its own in-memory result regardless of which write to
    # token.json ends up "winning". Same reasoning as gmail_client.py's
    # get_credentials.
    flow = InstalledAppFlow.from_client_secrets_file(str(source), SCOPES)
    creds = flow.run_local_server(port=0, timeout_seconds=300)
    _save_credentials(creds)
    return creds


def get_service():
    return build("calendar", "v3", credentials=get_credentials())


def parse_freebusy_response(response: dict, calendar_id: str = "primary") -> list[tuple[datetime, datetime]]:
    """Pulled out as its own function so the response-shape assumption can
    be unit-tested against a fixed sample without a real API call - the
    live Calendar API wasn't reachable to verify against from this
    environment (see docs/multi-provider-accounts-scope.md)."""
    busy_blocks = response.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    return [
        (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
        for b in busy_blocks
    ]


def get_busy_intervals(start: datetime, end: datetime, calendar_id: str = "primary") -> list[tuple[datetime, datetime]]:
    """Calls freebusy.query for [start, end) and returns the busy
    intervals only - never event titles/details (see module docstring)."""
    service = get_service()
    try:
        response = service.freebusy().query(body={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": calendar_id}],
        }).execute()
    except HttpError as exc:
        raise GoogleCalendarNotConfigured(f"Calendar free/busy lookup failed: {exc}") from exc
    return parse_freebusy_response(response, calendar_id)


@dataclass
class TimeWindow:
    start: datetime
    end: datetime

    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


def compute_free_windows(
    range_start: datetime,
    range_end: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
    business_start_hour: int = 9,
    business_end_hour: int = 17,
    min_duration_minutes: int = 30,
) -> list[TimeWindow]:
    """Pure function: given busy intervals within [range_start, range_end),
    returns the free windows inside business hours on each day, dropping
    weekends and anything shorter than min_duration_minutes. No API calls -
    this is the piece that's fully unit-testable without live Calendar
    access."""
    windows: list[TimeWindow] = []
    day = range_start.date()
    while day <= range_end.date():
        if day.weekday() < 5:  # Mon-Fri only - CTA replies aren't offering weekend interview slots
            day_start = datetime.combine(day, datetime.min.time()).replace(hour=business_start_hour)
            day_end = datetime.combine(day, datetime.min.time()).replace(hour=business_end_hour)
            day_busy = sorted(
                (max(b_start, day_start), min(b_end, day_end))
                for b_start, b_end in busy_intervals
                if b_start < day_end and b_end > day_start
            )
            cursor = day_start
            for b_start, b_end in day_busy:
                if b_start > cursor:
                    gap = TimeWindow(cursor, b_start)
                    if gap.duration_minutes() >= min_duration_minutes:
                        windows.append(gap)
                cursor = max(cursor, b_end)
            if cursor < day_end:
                gap = TimeWindow(cursor, day_end)
                if gap.duration_minutes() >= min_duration_minutes:
                    windows.append(gap)
        day += timedelta(days=1)
    return windows


def curate_believable_slots(
    free_windows: list[TimeWindow],
    count: int = 3,
    slot_duration_minutes: int = 30,
) -> list[TimeWindow]:
    """The "show a believable subset, not everything open" heuristic (see
    docs/multi-provider-accounts-scope.md Part B) - a genuinely open
    calendar shouldn't be offered in full, since that reads as "I have
    nothing else going on." Rules, deliberately simple (product-design
    heuristic, not an algorithm to over-engineer - expect to iterate with
    Zahir on how this actually feels):
      - at most one slot per day, so the offered times spread across
        different days instead of clustering
      - skip the very first available day if there's a later one to use
        instead (looking bookable the SAME day can itself read as
        "wide open" - offering day 2 onward first reads as "let me check
        my schedule" rather than "I'm free right now")
      - cap at `count` slots total, in chronological order
    Returns each picked window trimmed to slot_duration_minutes, starting
    from that window's own start time.
    """
    by_day: dict[date, TimeWindow] = {}
    for window in free_windows:
        day = window.start.date()
        if day not in by_day and window.duration_minutes() >= slot_duration_minutes:
            by_day[day] = window

    days = sorted(by_day.keys())
    if len(days) > count:
        days = days[1:]  # skip the earliest day when there's enough choice to spare it
    days = days[:count]

    return [
        TimeWindow(by_day[day].start, by_day[day].start + timedelta(minutes=slot_duration_minutes))
        for day in days
    ]
