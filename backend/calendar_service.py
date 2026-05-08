import os
import json
import base64
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from models import Appointment

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

BASE_DIR         = Path(__file__).parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH       = BASE_DIR / "token.json"


def get_calendar_service():
    """
    Authenticate and return a Google Calendar service object.
    Tries token loading in this order:
      1. GOOGLE_TOKEN_JSON env var (plain JSON — most reliable)
      2. GOOGLE_TOKEN_B64 env var (base64 encoded)
      3. Local token.json file (development)
    Also writes credentials.json from GOOGLE_CREDENTIALS_B64 if needed.
    """

    # ── Write credentials.json from env if missing ────────────────
    creds_b64 = os.getenv("GOOGLE_CREDENTIALS_B64")
    if creds_b64 and not CREDENTIALS_PATH.exists():
        try:
            creds_b64_fixed = creds_b64.strip()
            padding = 4 - len(creds_b64_fixed) % 4
            if padding != 4:
                creds_b64_fixed += "=" * padding
            creds_data = base64.b64decode(creds_b64_fixed).decode("utf-8")
            with open(CREDENTIALS_PATH, "w") as f:
                f.write(creds_data)
            print("[Calendar] ✅ credentials.json written from environment")
        except Exception as e:
            print(f"[Calendar] ⚠️ Could not write credentials.json: {e}")

    creds = None

    # ── Method 1: Plain JSON token from env (most reliable) ───────
    token_json_str = os.getenv("GOOGLE_TOKEN_JSON")
    if token_json_str:
        try:
            token_data = json.loads(token_json_str.strip())
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
            print("[Calendar] ✅ Loaded token from GOOGLE_TOKEN_JSON")
        except Exception as e:
            print(f"[Calendar] ⚠️ GOOGLE_TOKEN_JSON failed: {e}")
            creds = None

    # ── Method 2: Base64 encoded token from env (fallback) ────────
    if not creds:
        token_b64 = os.getenv("GOOGLE_TOKEN_B64")
        if token_b64:
            try:
                token_b64_fixed = token_b64.strip()
                padding = 4 - len(token_b64_fixed) % 4
                if padding != 4:
                    token_b64_fixed += "=" * padding
                token_json = base64.b64decode(token_b64_fixed).decode("utf-8")
                token_data  = json.loads(token_json)
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                print("[Calendar] ✅ Loaded token from GOOGLE_TOKEN_B64")
            except Exception as e:
                print(f"[Calendar] ⚠️ GOOGLE_TOKEN_B64 failed: {e}")
                creds = None

    # ── Method 3: Local token.json (development only) ─────────────
    if not creds:
        if TOKEN_PATH.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(TOKEN_PATH), SCOPES
                )
                print("[Calendar] ✅ Loaded token from token.json")
            except Exception as e:
                print(f"[Calendar] ⚠️ token.json failed: {e}")
                creds = None

    # ── Refresh if expired ────────────────────────────────────────
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            print("[Calendar] ✅ Token refreshed successfully")
        except Exception as e:
            print(f"[Calendar] ⚠️ Token refresh failed: {e}")
            creds = None

    # ── Local OAuth flow — only works in development ──────────────
    if not creds or not creds.valid:
        if not CREDENTIALS_PATH.exists():
            raise FileNotFoundError(
                "No valid Google credentials found.\n"
                "Set GOOGLE_TOKEN_JSON environment variable on Render.\n"
                "Locally: run python calendar_service.py to authenticate."
            )
        print("[Calendar] Starting OAuth flow...")
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_PATH), SCOPES
        )
        creds = flow.run_local_server(port=8080)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        print("[Calendar] ✅ OAuth complete — token.json saved")

    return build("calendar", "v3", credentials=creds)


def get_todays_appointments() -> List[Appointment]:
    """
    Fetch all appointments from Google Calendar for today.
    Returns a list of Appointment objects sorted by start time.
    """
    service = get_calendar_service()

    ist_offset  = timezone(timedelta(hours=5, minutes=30))
    now         = datetime.now(ist_offset)
    start_of_day = now.replace(hour=0,  minute=0,  second=0,  microsecond=0)
    end_of_day   = now.replace(hour=23, minute=59, second=59, microsecond=0)

    time_min = start_of_day.astimezone(timezone.utc).isoformat()
    time_max = end_of_day.astimezone(timezone.utc).isoformat()

    print(f"[Calendar] Fetching appointments {time_min} → {time_max}")

    try:
        events_result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])
        print(f"[Calendar] Found {len(events)} events today")

        appointments = []
        for event in events:
            appointment = parse_event(event)
            if appointment:
                appointments.append(appointment)

        return appointments

    except Exception as e:
        print(f"[Calendar] Error fetching events: {e}")
        raise


def parse_event(event: dict) -> Optional[Appointment]:
    """
    Convert a raw Google Calendar event into an Appointment object.
    Skips all-day events and events without a location.
    """
    start = event.get("start", {})
    end   = event.get("end",   {})

    if "dateTime" not in start:
        print(f"[Calendar] Skipping all-day event: {event.get('summary', 'Unnamed')}")
        return None

    location = event.get("location", "").strip()
    if not location:
        print(f"[Calendar] Skipping event with no location: {event.get('summary', 'Unnamed')}")
        return None

    try:
        start_time = datetime.fromisoformat(start["dateTime"])
        end_time   = datetime.fromisoformat(end["dateTime"])

        appointment = Appointment(
            id=event["id"],
            title=event.get("summary", "Unnamed Meeting"),
            location=location,
            start_time=start_time,
            end_time=end_time,
            description=event.get("description", ""),
        )

        print(f"[Calendar] Parsed: '{appointment.title}' at {appointment.location} — {start_time.strftime('%I:%M %p')}")
        return appointment

    except Exception as e:
        print(f"[Calendar] Error parsing event {event.get('summary')}: {e}")
        return None


def get_departure_window(appointment: Appointment, travel_minutes: int) -> datetime:
    """Calculate when the user needs to leave to arrive on time."""
    buffer_minutes = 5
    return appointment.start_time - timedelta(minutes=travel_minutes + buffer_minutes)


def get_appointments_needing_alert(
    appointments: List[Appointment],
    alert_minutes_before: int = 30,
) -> List[Appointment]:
    """
    Return appointments whose alert window is NOW (within 60 seconds).
    Used by the scheduler to decide when to fire alerts.
    """
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    now        = datetime.now(ist_offset)
    due        = []

    for appt in appointments:
        start = appt.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=ist_offset)
        alert_time = start - timedelta(minutes=alert_minutes_before)
        if abs((now - alert_time).total_seconds()) <= 60:
            due.append(appt)

    return due


# ── Quick test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing Google Calendar connection...")
    appointments = get_todays_appointments()

    if not appointments:
        print("\nNo appointments with locations found for today.")
        print("Add a test event in Google Calendar with a location and re-run.")
    else:
        print(f"\n✅ Successfully fetched {len(appointments)} appointments:")
        for appt in appointments:
            print(f"  • {appt.title}")
            print(f"    Location : {appt.location}")
            print(f"    Time     : {appt.start_time.strftime('%I:%M %p')} → {appt.end_time.strftime('%I:%M %p')}")