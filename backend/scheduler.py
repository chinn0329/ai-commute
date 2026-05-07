import os
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from dotenv import load_dotenv

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from alert_service import get_user_current_location
load_dotenv(Path(__file__).parent.parent / ".env")

from calendar_service  import get_todays_appointments
from routing_service   import get_route_options, get_best_route
from weather_service   import get_bangalore_weather, get_weather_mode_overrides
from reasoning_service import reason_full_day, reason_single_appointment, reason_reroute
from alert_service     import (
    send_morning_briefing,
    send_departure_alert,
    send_reroute_alert,
    send_conflict_alert,
)
from models import Appointment, RouteOption, UserPreferences

IST = timezone(timedelta(hours=5, minutes=30))

# ── Global state (in-memory for hackathon) ────────────────────────
active_commutes: dict = {}   # appointment_id → RouteOption currently being used
todays_appointments: list = []
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

# Default preferences — in full app this comes from DB/dashboard
DEFAULT_PREFERENCES = UserPreferences(
    user_id="default",
    allowed_modes=["metro", "bus", "auto", "cab", "walk"],
    optimization="fastest",
    avoid_cabs=False,
    eco_friendly=False,
    has_luggage=False,
    low_battery=False,
)

async def wait_for_user_location(
    timeout_seconds: int = 600,
    poll_interval: int = 5,
) -> str:
    """
    Poll Telegram every `poll_interval` seconds until:
      - User shares a GPS pin   → returns "lat,lon"
      - User types an address   → returns that text
      - Timeout reached         → returns .env fallback
    Fires immediately when location arrives — no fixed sleep.
    """
    from alert_service import get_telegram_updates
    fallback = os.getenv("USER_HOME_LOCATION", "Indiranagar, Bangalore")
    last_update_id = None
    elapsed = 0

    while elapsed < timeout_seconds:
        updates = await get_telegram_updates(offset=last_update_id)

        for update in updates:
            last_update_id = update["update_id"] + 1  # advance offset so we don't re-read
            message = update.get("message", {})

            # Priority 1: GPS location pin
            location = message.get("location")
            if location:
                lat = location["latitude"]
                lon = location["longitude"]
                loc_str = f"{lat},{lon}"
                print(f"[Scheduler] ✅ GPS location received immediately: {loc_str}")
                return loc_str

            # Priority 2: Text address (ignore commands)
            text = message.get("text", "").strip()
            if text and not text.startswith("/"):
                print(f"[Scheduler] ✅ Text location received: {text}")
                return text

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        remaining = timeout_seconds - elapsed
        if elapsed % 60 == 0:  # log every minute so you can see it's alive
            print(f"[Scheduler] Still waiting for location... ({remaining}s remaining)")

    print(f"[Scheduler] ⏱ Timeout — using fallback: {fallback}")
    return fallback
# ════════════════════════════════════════════════════════════════
# MORNING ANALYSIS — runs at 5:00 AM daily
# ════════════════════════════════════════════════════════════════
async def run_morning_analysis():
    """
    Main morning job:
    1. Request user location via Telegram
    2. Wait for user to share location
    3. Read Google Calendar
    4. Fetch routes for all appointments
    5. Run Groq full-day reasoning
    6. Send morning briefing
    7. Schedule departure alerts
    """
    global todays_appointments

    print(f"\n{'='*50}")
    print(f"[Scheduler] Morning analysis — {datetime.now(IST).strftime('%I:%M %p')}")
    print(f"{'='*50}")

    try:
        # Step 1: Ask user to share location
        from alert_service import request_user_location, get_user_current_location
        await request_user_location()

        # Step 2: Poll for location — fires as soon as user shares, max 10 min wait
        print("[Scheduler] Waiting for user to share location (max 10 min)...")
        user_location = await wait_for_user_location(timeout_seconds=600, poll_interval=5)
        print(f"[Scheduler] Using location: {user_location}")

        # Step 4: Get appointments
        appointments = get_todays_appointments()
        todays_appointments = appointments

        if not appointments:
            print("[Scheduler] No appointments today")
            from alert_service import send_telegram_message
            await send_telegram_message(
                "🌅 <b>Good Morning!</b>\n\n"
                "No appointments with locations found in your calendar today.\n"
                "Add events with a location in Google Calendar to get commute alerts.\n\n"
                "<i>— AI-Commute</i>"
            )
            return

        # Step 5: Get weather
        weather = await get_bangalore_weather()
        avoid_modes = get_weather_mode_overrides(weather)

        # Step 6: Get routes for all appointments
        all_routes = {}
        for appt in appointments:
            routes = await get_route_options(
                origin=user_location,
                destination=appt.location,
                departure_time=appt.start_time - timedelta(hours=1),
                preferences=DEFAULT_PREFERENCES,
                weather_avoid_modes=avoid_modes,
            )
            all_routes[appt.id] = routes

        # Step 7: Full day Groq reasoning
        day_plan = await reason_full_day(
            appointments, all_routes, weather, DEFAULT_PREFERENCES
        )

        # Step 8: Send morning briefing
        await send_morning_briefing(day_plan)

        # Step 9: Send conflict alerts
        for i, conflict in enumerate(day_plan.conflicts):
            if i < len(appointments):
                await send_conflict_alert(conflict, appointments[i].title)

        # Step 10: Schedule departure alerts
        for appt in appointments:
            schedule_departure_alert(
                appt, all_routes.get(appt.id, []), weather, user_location
            )

        print("[Scheduler] Morning analysis complete ✅")

    except Exception as e:
        print(f"[Scheduler] Morning analysis error: {e}")
        import traceback
        traceback.print_exc()

# ════════════════════════════════════════════════════════════════
# DEPARTURE ALERT — scheduled per appointment
# ════════════════════════════════════════════════════════════════
def schedule_departure_alert(
    appointment: Appointment,
    routes: List[RouteOption],
    weather: dict,
    user_location: str,
    alert_minutes_before: int = 30,
):
    alert_time = appointment.start_time - timedelta(minutes=alert_minutes_before)
    now        = datetime.now(IST)

    if alert_time <= now:
        print(f"[Scheduler] Alert time passed for '{appointment.title}' — skipping")
        return

    job_id = f"alert_{appointment.id}"

    async def fire_alert():
        try:
            print(f"[Scheduler] Firing departure alert for '{appointment.title}'")

            # Get fresh location at alert time
            from alert_service import get_user_current_location
            fresh_location = await get_user_current_location()

            # Re-fetch routes with fresh location
            fresh_routes = await get_route_options(
                origin=fresh_location,
                destination=appointment.location,
                departure_time=datetime.now(IST) + timedelta(minutes=30),
                preferences=DEFAULT_PREFERENCES,
                weather_avoid_modes=get_weather_mode_overrides(weather),
            )

            final_routes = fresh_routes if fresh_routes else routes
            result = await reason_single_appointment(
                appointment, final_routes, weather, DEFAULT_PREFERENCES
            )
            await send_departure_alert(appointment, result, all_routes=final_routes)

            best = get_best_route(final_routes)
            if best:
                active_commutes[appointment.id] = best

        except Exception as e:
            print(f"[Scheduler] Alert error: {e}")

    scheduler.add_job(
        fire_alert,
        trigger="date",
        run_date=alert_time,
        id=job_id,
        replace_existing=True,
    )
    print(f"[Scheduler] Alert scheduled for '{appointment.title}' at {alert_time.strftime('%I:%M %p')}")
# ════════════════════════════════════════════════════════════════
# TRAFFIC RE-CHECK — runs every 10 minutes
# ════════════════════════════════════════════════════════════════
async def run_traffic_recheck():
    """
    Re-check traffic every 10 minutes for active commutes.
    If ETA shifts by more than 15 minutes → send re-route alert.
    """
    if not active_commutes:
        return

    now = datetime.now(IST)
    print(f"[Scheduler] Traffic re-check — {now.strftime('%I:%M %p')} — {len(active_commutes)} active commutes")

    weather = await get_bangalore_weather()
    user_location = await get_user_current_location()
    for appt_id, original_route in list(active_commutes.items()):
        # Find the appointment
        appt = next((a for a in todays_appointments if a.id == appt_id), None)
        if not appt:
            continue

        # Skip if meeting already started
        if now >= appt.start_time:
            del active_commutes[appt_id]
            continue

        try:
            # Get fresh routes
            new_routes = await get_route_options(
                # Try to get live location from Telegram, fall back to .env
                
                origin = user_location or os.getenv("USER_HOME_LOCATION", "Indiranagar, Bangalore"),
                destination=appt.location,
                departure_time=now,
                preferences=DEFAULT_PREFERENCES,
                weather_avoid_modes=get_weather_mode_overrides(weather),
            )

            if not new_routes:
                continue

            # Compare ETAs
            best_new = get_best_route(new_routes)
            if not best_new:
                continue

            delay = best_new.duration_minutes - original_route.duration_minutes

            print(f"[Scheduler] '{appt.title}': original {original_route.duration_minutes}min, now {best_new.duration_minutes}min, delay +{delay}min")

            # Re-route threshold: 15 minutes
            if delay >= 15:
                print(f"[Scheduler] ⚠️ Significant delay detected — sending re-route alert")
                reroute = await reason_reroute(
                    appt, original_route, new_routes, delay, weather
                )
                await send_reroute_alert(appt, reroute)

                # Update active commute to new route
                active_commutes[appt_id] = best_new

        except Exception as e:
            print(f"[Scheduler] Re-check error for '{appt_id}': {e}")


# ════════════════════════════════════════════════════════════════
# START SCHEDULER
# ════════════════════════════════════════════════════════════════
def start_scheduler():
    """
    Register all scheduled jobs and start the scheduler.
    """
    # Morning analysis at 5:00 AM daily
    scheduler.add_job(
        run_morning_analysis,
        CronTrigger(hour=5, minute=0, timezone="Asia/Kolkata"),
        id="morning_analysis",
        replace_existing=True,
    )

    # Traffic re-check every 10 minutes
    scheduler.add_job(
        run_traffic_recheck,
        IntervalTrigger(minutes=10),
        id="traffic_recheck",
        replace_existing=True,
    )

    scheduler.start()
    print("[Scheduler] ✅ Scheduler started")
    print("[Scheduler]    Morning analysis: 5:00 AM daily")
    print("[Scheduler]    Traffic re-check: every 10 minutes")


# ── Manual trigger for testing ────────────────────────────────────
if __name__ == "__main__":
    async def test():
        print("Running morning analysis NOW (manual trigger for testing)...\n")
        await run_morning_analysis()
        print("\n✅ Manual morning analysis complete")

    async def test_departure_alert_now():
        """
        Fires a departure alert immediately for the first appointment found.
        Use this to test route display in Telegram without waiting 30 min.
        """
        print("Testing departure alert NOW...\n")

        from alert_service import request_user_location
        await request_user_location()

        print("Waiting for your location (max 2 min)...")
        user_location = await wait_for_user_location(timeout_seconds=120, poll_interval=3)
        print(f"Location: {user_location}")

        appointments = get_todays_appointments()
        if not appointments:
            print("No appointments found in calendar today.")
            return

        appt = appointments[0]
        print(f"Using appointment: {appt.title} at {appt.location}")

        weather = await get_bangalore_weather()
        avoid_modes = get_weather_mode_overrides(weather)

        routes = await get_route_options(
            origin=user_location,
            destination=appt.location,
            departure_time=datetime.now(IST) + timedelta(minutes=30),
            preferences=DEFAULT_PREFERENCES,
            weather_avoid_modes=avoid_modes,
        )

        print(f"\nRoutes fetched: {len(routes)}")
        for r in routes:
            print(f"  {r.mode} — {r.duration_minutes}min — ₹{r.cost_inr}")

        result = await reason_single_appointment(appt, routes, weather, DEFAULT_PREFERENCES)
        await send_departure_alert(appt, result, all_routes=routes)
        print("\n✅ Departure alert sent — check Telegram!")

    # ← CHANGE THIS LINE to switch between tests
    asyncio.run(test())