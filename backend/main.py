import os
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from calendar_service  import get_todays_appointments
from routing_service   import get_route_options, get_best_route
from weather_service   import get_bangalore_weather, get_weather_mode_overrides
from reasoning_service import reason_full_day, reason_single_appointment
from alert_service     import (
    send_telegram_message,
    get_user_current_location,
    request_user_location,
    handle_user_reply,
    send_departure_alert,
    get_telegram_updates,
)
from scheduler import start_scheduler, run_morning_analysis
from models    import UserPreferences, DayPlan

IST = timezone(timedelta(hours=5, minutes=30))

# ── Default preferences ───────────────────────────────────────────
DEFAULT_PREFERENCES = UserPreferences(
    user_id="default",
    allowed_modes=["metro", "bus", "auto", "cab", "walk"],
    optimization="fastest",
    avoid_cabs=False,
    eco_friendly=False,
    has_luggage=False,
    low_battery=False,
)

# ── Track last processed Telegram update ─────────────────────────
last_update_id: int = 0


# ════════════════════════════════════════════════════════════════
# STARTUP & SHUTDOWN
# ════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs on startup and shutdown.
    Starts the scheduler and Telegram polling loop.
    """
    print("\n" + "="*50)
    print("  AI-Commute Backend Starting...")
    print("="*50)

    # Start APScheduler
    start_scheduler()

    # Start Telegram reply listener in background
    asyncio.create_task(telegram_reply_listener())

    print("✅ AI-Commute is running!")
    print("📱 Send your location in Telegram to get started")
    print("="*50 + "\n")

    yield  # App runs here

    print("\n[App] Shutting down AI-Commute...")


# ── FastAPI app ───────────────────────────────────────────────────
app = FastAPI(
    title="AI-Commute API",
    description="AI-Powered Day Logistics Agent for Bangalore Professionals",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow frontend to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ════════════════════════════════════════════════════════════════
# TELEGRAM REPLY LISTENER
# ════════════════════════════════════════════════════════════════
async def telegram_reply_listener():
    """
    Continuously polls Telegram for user replies.
    Handles numbered replies (1, 2, 3) and location shares.
    Runs every 5 seconds in the background.
    """
    global last_update_id
    print("[Telegram] Reply listener started")

    while True:
        try:
            updates = await get_telegram_updates(offset=last_update_id + 1)

            for update in updates:
                update_id = update.get("update_id", 0)
                last_update_id = max(last_update_id, update_id)

                message = update.get("message", {})
                text    = message.get("text", "").strip()
                location = message.get("location")

                # Handle location share
                if location:
                    lat = location["latitude"]
                    lon = location["longitude"]
                    print(f"[Telegram] 📍 Location received: {lat}, {lon}")
                    await send_telegram_message(
                        f"📍 <b>Location received!</b>\n"
                        f"Coordinates: {lat:.4f}, {lon:.4f}\n"
                        f"I'll use this for your commute planning. ✅"
                    )

                # Handle numbered replies
                elif text in ["1", "2", "3"]:
                    print(f"[Telegram] User replied: {text}")
                    # Get current active appointment context
                    from scheduler import todays_appointments, active_commutes
                    current_appt = None
                    if todays_appointments:
                        now = datetime.now(IST)
                        # Find the most relevant upcoming appointment
                        upcoming = [
                            a for a in todays_appointments
                            if a.start_time > now
                        ]
                        if upcoming:
                            current_appt = upcoming[0]

                    appt_title = current_appt.title if current_appt else "your meeting"
                    reply = await handle_user_reply(text, appt_title)
                    await send_telegram_message(reply)

                # Handle text address
                elif text and not text.startswith("/"):
                    print(f"[Telegram] Address received: {text}")
                    await send_telegram_message(
                        f"📍 <b>Location set!</b>\n"
                        f"Using: <b>{text}</b>\n"
                        f"I'll plan your commute from this location. ✅"
                    )

                # Handle /start command
                elif text == "/start":
                    await send_telegram_message(
                        "👋 <b>Welcome to AI-Commute!</b>\n\n"
                        "I'm your personal Bangalore commute assistant.\n\n"
                        "Here's what I do:\n"
                        "📅 Read your Google Calendar\n"
                        "🗺 Plan optimal routes across Bangalore\n"
                        "⚠️ Detect scheduling conflicts before they happen\n"
                        "🔄 Re-route you when traffic changes\n\n"
                        "To get started:\n"
                        "👉 Share your 📍 <b>location</b> or type your address\n"
                        "👉 Or type /analyse to run analysis now\n\n"
                        "<i>— AI-Commute, RVCE Hackathon 2026</i>"
                    )

                # Handle /analyse command
                elif text == "/analyse":
                    await send_telegram_message(
                        "🔄 <b>Running day analysis now...</b>\n"
                        "Please share your 📍 location first!"
                    )
                    await request_user_location()

                # Handle /help command
                elif text == "/help":
                    await send_telegram_message(
                        "🤖 <b>AI-Commute Commands:</b>\n\n"
                        "/start — Welcome message\n"
                        "/analyse — Run day analysis now\n"
                        "/status — Show today's appointments\n"
                        "/help — Show this message\n\n"
                        "📍 Share your location anytime to update commute plans.\n"
                        "Reply <b>1, 2, or 3</b> to act on conflict alerts."
                    )

                # Handle /status command
                elif text == "/status":
                    from scheduler import todays_appointments
                    if not todays_appointments:
                        await send_telegram_message(
                            "📅 No appointments loaded yet.\n"
                            "Type /analyse to run the morning analysis."
                        )
                    else:
                        now   = datetime.now(IST)
                        lines = ["📅 <b>Today's Appointments:</b>\n"]
                        for appt in todays_appointments:
                            time_str = appt.start_time.strftime("%I:%M %p")
                            status   = "✅" if appt.start_time < now else "🔜"
                            lines.append(f"{status} {time_str} — {appt.title}")
                        await send_telegram_message("\n".join(lines))

        except Exception as e:
            print(f"[Telegram] Listener error: {e}")

        await asyncio.sleep(5)  # Poll every 5 seconds


# ════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "app":     "AI-Commute",
        "status":  "running",
        "version": "1.0.0",
        "time":    datetime.now(IST).strftime("%I:%M %p IST"),
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now(IST).isoformat()}


@app.post("/analyse")
async def trigger_analysis(background_tasks: BackgroundTasks):
    """
    Manually trigger the morning analysis.
    Useful for testing without waiting for 5 AM.
    """
    background_tasks.add_task(run_morning_analysis)
    return {
        "status":  "started",
        "message": "Morning analysis triggered. Check Telegram for results.",
    }


@app.get("/appointments")
async def get_appointments():
    """
    Return today's appointments from Google Calendar.
    """
    try:
        appointments = get_todays_appointments()
        return {
            "count": len(appointments),
            "appointments": [
                {
                    "id":         a.id,
                    "title":      a.title,
                    "location":   a.location,
                    "start_time": a.start_time.strftime("%I:%M %p"),
                    "end_time":   a.end_time.strftime("%I:%M %p"),
                }
                for a in appointments
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/weather")
async def get_weather():
    """
    Return current Bangalore weather.
    """
    try:
        weather = await get_bangalore_weather()
        return {
            "temperature_c": weather["temperature_c"],
            "description":   weather["description"],
            "is_raining":    weather["is_raining"],
            "advisory":      weather["advisory"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/routes")
async def get_routes(origin: str, destination: str):
    """
    Get route options between two Bangalore locations.
    Example: /routes?origin=Indiranagar&destination=Whitefield
    """
    try:
        now    = datetime.now(IST)
        routes = await get_route_options(
            origin=origin,
            destination=destination,
            departure_time=now,
            preferences=DEFAULT_PREFERENCES,
        )
        return {
            "origin":      origin,
            "destination": destination,
            "routes": [
                {
                    "mode":             r.mode,
                    "duration_minutes": r.duration_minutes,
                    "distance_km":      r.distance_km,
                    "cost_inr":         r.cost_inr,
                    "departure_time":   r.departure_time.strftime("%I:%M %p"),
                    "arrival_time":     r.arrival_time.strftime("%I:%M %p"),
                    "steps":            r.steps,
                }
                for r in routes
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send-test-message")
async def send_test_message():
    """
    Send a test message to Telegram to verify connection.
    """
    success = await send_telegram_message(
        "🤖 <b>AI-Commute Test Message</b>\n\n"
        "✅ Your backend is running!\n"
        f"🕐 Time: {datetime.now(IST).strftime('%I:%M %p IST')}\n\n"
        "<i>— AI-Commute API</i>"
    )
    return {"success": success}


@app.post("/preferences")
async def update_preferences(preferences: UserPreferences):
    """
    Update user commute preferences.
    """
    global DEFAULT_PREFERENCES
    DEFAULT_PREFERENCES = preferences
    return {
        "status":  "updated",
        "message": "Preferences saved successfully",
        "preferences": preferences.dict(),
    }


@app.get("/preferences")
async def get_preferences():
    """
    Get current user preferences.
    """
    return DEFAULT_PREFERENCES.dict()