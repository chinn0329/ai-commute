import os
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from models import Appointment

# If you modify these scopes, delete token.json and re-authenticate
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Paths
BASE_DIR = Path(__file__).parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"

# Production: write credentials.json from env variable


def get_calendar_service():
    """
    Authenticate and return a Google Calendar service object.
    First run: opens browser for OAuth login.
    After that: uses saved token.json automatically.
    """
    import base64, json as _json
    creds_b64 = os.getenv("GOOGLE_CREDENTIALS_B64")
    if creds_b64 and not CREDENTIALS_PATH.exists():
        creds_data = base64.b64decode(creds_b64).decode()
        with open(CREDENTIALS_PATH, "w") as f:
            f.write(creds_data)
        print("[Calendar] credentials.json written from environment")
        
    creds = None

    # Load saved token if it exists
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    # If no valid credentials, ask user to log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                raise FileNotFoundError(
                    "credentials.json not found in backend/ folder. "
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), SCOPES
            )
            creds = flow.run_local_server(port=8080)

        # Save token for future runs
        with open(TOKEN_PATH, "w") as token_file:
            token_file.write(creds.to_json())

    service = build("calendar", "v3", credentials=creds)
    return service


def get_todays_appointments() -> List[Appointment]:
    """
    Fetch all appointments from Google Calendar for today.
    Returns a list of Appointment objects sorted by start time.
    """
    service = get_calendar_service()

    # Get today's date range in IST (UTC+5:30)
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist_offset)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)

    # Convert to UTC for the API call
    time_min = start_of_day.astimezone(timezone.utc).isoformat()
    time_max = end_of_day.astimezone(timezone.utc).isoformat()

    print(f"[Calendar] Fetching appointments from {time_min} to {time_max}")

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
    Convert a raw Google Calendar event dict into an Appointment object.
    Skips all-day events (no specific time) and events without a location.
    """
    # Skip all-day events
    start = event.get("start", {})
    end = event.get("end", {})

    if "dateTime" not in start:
        print(f"[Calendar] Skipping all-day event: {event.get('summary', 'Unnamed')}")
        return None

    # Skip events without a location
    location = event.get("location", "").strip()
    if not location:
        print(f"[Calendar] Skipping event with no location: {event.get('summary', 'Unnamed')}")
        return None

    try:
        start_time = datetime.fromisoformat(start["dateTime"])
        end_time = datetime.fromisoformat(end["dateTime"])

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
    """
    Calculate when the user needs to leave to arrive on time.
    Adds a 5-minute buffer on top of travel time.
    """
    buffer_minutes = 5
    departure = appointment.start_time - timedelta(minutes=travel_minutes + buffer_minutes)
    return departure


def get_appointments_needing_alert(
    appointments: List[Appointment],
    alert_minutes_before: int = 30
) -> List[Appointment]:
    """
    From a list of appointments, return only those whose
    alert window is NOW (within a 1-minute window of the alert time).
    Used by the scheduler to decide when to fire alerts.
    """
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist_offset)

    due = []
    for appt in appointments:
        # Make start_time timezone-aware if it isn't
        start = appt.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=ist_offset)

        alert_time = start - timedelta(minutes=alert_minutes_before)
        diff = abs((now - alert_time).total_seconds())

        # Fire if within 60 seconds of alert time
        if diff <= 60:
            due.append(appt)

    return due


# ── Quick test — run this file directly to verify calendar works ──
if __name__ == "__main__":
    print("Testing Google Calendar connection...")
    appointments = get_todays_appointments()

    if not appointments:
        print("\nNo appointments with locations found for today.")
        print("Add a test event in Google Calendar with a Bangalore address and re-run.")
    else:
        print(f"\n✅ Successfully fetched {len(appointments)} appointments:")
        for appt in appointments:
            print(f"  • {appt.title}")
            print(f"    Location : {appt.location}")
            print(f"    Time     : {appt.start_time.strftime('%I:%M %p')} → {appt.end_time.strftime('%I:%M %p')}")