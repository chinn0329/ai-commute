import os
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from dotenv import load_dotenv

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

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


# ════════════════════════════════════════════════════════════════
# MORNING ANALYSIS — runs at 5:00 AM daily
# ════════════════════════════════════════════════════════════════
async def run_morning_analysis():
    """
    Main morning job:
    1. Read Google Calendar
    2. Fetch routes for all appointments
    3. Run Groq full-day reasoning
    4. Send morning briefing to Telegram
    5. Schedule departure alerts for each appointment
    """
    global todays_appointments

    print(f"\n{'='*50}")
    print(f"[Scheduler] Morning analysis starting — {datetime.now(IST).strftime('%I:%M %p')}")
    print(f"{'='*50}")

    try:
        # Step 1: Get appointments
        appointments = get_todays_appointments()
        todays_appointments = appointments

        if not appointments:
            print("[Scheduler] No appointments today — skipping analysis")
            return

        print(f"[Scheduler] Found {len(appointments)} appointments")

        # Step 2: Get weather
        weather = await get_bangalore_weather()
        avoid_modes = get_weather_mode_overrides(weather)

        # Step 3: Get routes for all appointments
        all_routes = {}
        for appt in appointments:
            routes = await get_route_options(
                origin="your home location",   # in full app: from user profile
                destination=appt.location,
                departure_time=appt.start_time - timedelta(hours=1),
                preferences=DEFAULT_PREFERENCES,
                weather_avoid_modes=avoid_modes,
            )
            all_routes[appt.id] = routes

        # Step 4: Full day Groq reasoning
        day_plan = await reason_full_day(
            appointments, all_routes, weather, DEFAULT_PREFERENCES
        )

        # Step 5: Send morning briefing
        await send_morning_briefing(day_plan)

        # Step 6: Send conflict alerts if any
        for i, conflict in enumerate(day_plan.conflicts):
            if i < len(appointments):
                await send_conflict_alert(conflict, appointments[i].title)

        # Step 7: Schedule individual departure alerts
        for appt in appointments:
            schedule_departure_alert(appt, all_routes.get(appt.id, []), weather)

        print(f"[Scheduler] Morning analysis complete ✅")

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
    alert_minutes_before: int = 30,
):
    """
    Schedule a one-time departure alert for a specific appointment.
    Fires 30 minutes before the appointment start time.
    """
    alert_time = appointment.start_time - timedelta(minutes=alert_minutes_before)
    now = datetime.now(IST)

    # Only schedule if alert time is in the future
    if alert_time <= now:
        print(f"[Scheduler] Alert time for '{appointment.title}' already passed — skipping")
        return

    job_id = f"alert_{appointment.id}"

    async def fire_alert():
        try:
            print(f"[Scheduler] Firing departure alert for '{appointment.title}'")
            result = await reason_single_appointment(
                appointment, routes, weather, DEFAULT_PREFERENCES
            )
            await send_departure_alert(appointment, result)

            # Store active commute for re-routing tracking
            best = get_best_route(routes)
            if best:
                active_commutes[appointment.id] = best

        except Exception as e:
            print(f"[Scheduler] Alert error for '{appointment.title}': {e}")

    scheduler.add_job(
        fire_alert,
        trigger="date",
        run_date=alert_time,
        id=job_id,
        replace_existing=True,
    )
    print(f"[Scheduler] Departure alert scheduled for '{appointment.title}' at {alert_time.strftime('%I:%M %p')}")


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
                origin="current location",
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

    asyncio.run(test())