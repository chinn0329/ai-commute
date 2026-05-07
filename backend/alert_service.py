import os
import httpx
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API       = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Global — stores the latest known user location
latest_user_location: str = None


async def send_telegram_message(text: str, chat_id: str = None) -> bool:
    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        print("[Alert] No TELEGRAM_CHAT_ID set in .env")
        return False

    url     = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id":    target,
        "text":       text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            print(f"[Alert] ✅ Telegram message sent ({len(text)} chars)")
            return True
    except Exception as e:
        print(f"[Alert] ❌ Failed to send Telegram message: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# LOCATION HANDLING
# ════════════════════════════════════════════════════════════════
async def get_telegram_updates(offset: int = None) -> list:
    """
    Poll Telegram for new messages and location shares.
    """
    url    = f"{TELEGRAM_API}/getUpdates"
    params = {"limit": 10, "timeout": 1}
    if offset:
        params["offset"] = offset

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json().get("result", [])
    except Exception as e:
        print(f"[Alert] Error getting updates: {e}")
        return []


async def get_user_current_location() -> str:
    """
    Check Telegram updates for a location shared by the user.
    Checks three things in order:
      1. A Telegram location pin (most accurate — GPS)
      2. A text message starting with an address
      3. Falls back to USER_HOME_LOCATION in .env
    Returns a string usable as routing origin.
    """
    global latest_user_location

    updates = await get_telegram_updates()

    for update in reversed(updates):
        message = update.get("message", {})

        # ── Priority 1: Telegram native location share (GPS) ──
        location = message.get("location")
        if location:
            lat = location["latitude"]
            lon = location["longitude"]
            location_str = f"{lat},{lon}"
            latest_user_location = location_str
            print(f"[Location] 📍 GPS location received: {lat}, {lon}")
            return location_str

        # ── Priority 2: Text message with address ──
        text = message.get("text", "").strip()
        if text and not text.startswith("/"):
            # Treat any non-command text as a location
            latest_user_location = text
            print(f"[Location] 📍 Text location received: {text}")
            return text

    # ── Priority 3: Last known location ──
    if latest_user_location:
        print(f"[Location] 📍 Using last known location: {latest_user_location}")
        return latest_user_location

    # ── Priority 4: .env fallback ──
    fallback = os.getenv("USER_HOME_LOCATION", "Indiranagar, Bangalore")
    print(f"[Location] 📍 Using .env fallback: {fallback}")
    return fallback


async def request_user_location() -> bool:
    """
    Send a message asking the user to share their current location.
    Called at the start of morning analysis.
    """
    message = (
        "📍 <b>AI-Commute needs your current location</b>\n"
        "\n"
        "Please share your location so I can plan your commute:\n"
        "\n"
        "👉 Tap the <b>paperclip / attachment icon</b>\n"
        "👉 Select <b>Location</b>\n"
        "👉 Tap <b>Send My Current Location</b>\n"
        "\n"
        "Or just <b>type your current address</b> and send it.\n"
        "\n"
        "<i>Example: Koramangala 5th Block, Bangalore</i>"
    )
    return await send_telegram_message(message)


async def handle_user_reply(text: str, appointment_title: str) -> str:
    """
    Process numbered replies from conflict/reroute alerts.
    Returns a confirmation message to send back.
    """
    text = text.strip()

    if text == "1":
        return (
            f"✅ Got it! Leaving early from current meeting.\n"
            f"I'll update your route to <b>{appointment_title}</b> shortly."
        )
    elif text == "2":
        return (
            f"📅 Noted. I'll help you draft a reschedule message for "
            f"<b>{appointment_title}</b>.\n"
            f"Send the organiser's name and I'll prepare the message."
        )
    elif text == "3":
        return f"👍 Understood. You're handling <b>{appointment_title}</b> yourself."
    else:
        return (
            f"🤖 <b>AI-Commute commands:</b>\n"
            f"Reply <b>1</b> — Leave early\n"
            f"Reply <b>2</b> — Reschedule meeting\n"
            f"Reply <b>3</b> — Handle it myself\n"
            f"Or share your 📍 location anytime to update your commute plan."
        )


# ════════════════════════════════════════════════════════════════
# ALERT SENDERS
# ════════════════════════════════════════════════════════════════
async def send_morning_briefing(day_plan) -> bool:
    score_pct = int(day_plan.feasibility_score * 100)

    if score_pct >= 80:
        emoji = "🟢"
    elif score_pct >= 60:
        emoji = "🟡"
    else:
        emoji = "🔴"

    lines = [
        f"🌅 <b>Good Morning! AI-Commute Day Briefing</b>",
        f"",
        f"{emoji} <b>Day Feasibility Score: {score_pct}%</b>",
        f"",
    ]

    if day_plan.appointments:
        lines.append(f"📅 <b>Today's Appointments ({len(day_plan.appointments)}):</b>")
        for appt in day_plan.appointments:
            time_str = appt.start_time.strftime("%I:%M %p")
            lines.append(f"  • {time_str} — {appt.title}")
        lines.append("")

    if day_plan.conflicts:
        lines.append("⚠️ <b>Conflicts Detected:</b>")
        for conflict in day_plan.conflicts[:2]:
            lines.append(f"  • {conflict}")
        lines.append("")

    if day_plan.recommendations:
        lines.append("💡 <b>Recommendations:</b>")
        for rec in day_plan.recommendations[:3]:
            lines.append(f"  • {rec}")
        lines.append("")

    if day_plan.weather_advisory:
        lines.append(f"🌦 <b>Weather:</b> {day_plan.weather_advisory}")

    return await send_telegram_message("\n".join(lines))


async def send_departure_alert(appointment, reasoning_result: dict) -> bool:
    mode_emojis = {
        "cab":               "🚕",
        "metro_auto_hybrid": "🚇",
        "bus":               "🚌",
        "walk":              "🚶",
        "metro_bus":         "🚇",
    }

    mode      = reasoning_result.get("best_mode", "cab")
    emoji     = mode_emojis.get(mode, "🚗")
    departure = reasoning_result.get("departure_time", "Now")
    duration  = reasoning_result.get("duration_minutes", "?")
    cost      = reasoning_result.get("cost_inr", "?")
    alert_msg = reasoning_result.get("alert_message", "")

    message = (
        f"{emoji} <b>Departure Alert — AI-Commute</b>\n"
        f"\n"
        f"📍 <b>{appointment.title}</b>\n"
        f"📌 {appointment.location}\n"
        f"🕐 Starts: {appointment.start_time.strftime('%I:%M %p')}\n"
        f"\n"
        f"🗺 <b>Best Route: {mode.replace('_', ' ').title()}</b>\n"
        f"⏱ Duration: {duration} min\n"
        f"💰 Cost: ₹{cost}\n"
        f"🚀 <b>Leave by: {departure}</b>\n"
        f"\n"
        f"{alert_msg}\n"
        f"\n"
        f"Reply <b>1</b> — I'm leaving now\n"
        f"Reply <b>2</b> — Need more time\n"
        f"Reply <b>3</b> — Skip this alert"
    )

    return await send_telegram_message(message)


async def send_reroute_alert(appointment, reroute_result: dict) -> bool:
    urgency_emoji = "🚨" if reroute_result.get("urgency") == "high" else "⚠️"
    late_text = ""
    if reroute_result.get("will_be_late"):
        mins = reroute_result.get("minutes_late", 0)
        late_text = f"\n⏰ You may be <b>{mins} min late</b> — consider notifying the organiser."

    message = (
        f"{urgency_emoji} <b>Route Update — AI-Commute</b>\n"
        f"\n"
        f"📍 <b>{appointment.title}</b> at {appointment.start_time.strftime('%I:%M %p')}\n"
        f"\n"
        f"{reroute_result.get('message', 'Traffic conditions have changed.')}"
        f"{late_text}\n"
        f"\n"
        f"Reply <b>1</b> — Switch to new route\n"
        f"Reply <b>2</b> — Stay on current route\n"
        f"Reply <b>3</b> — Reschedule meeting"
    )

    return await send_telegram_message(message)


async def send_conflict_alert(conflict: str, appointment_title: str) -> bool:
    message = (
        f"⚡ <b>Schedule Conflict Detected — AI-Commute</b>\n"
        f"\n"
        f"📍 Affected: <b>{appointment_title}</b>\n"
        f"\n"
        f"{conflict}\n"
        f"\n"
        f"Reply <b>1</b> — Leave previous meeting early\n"
        f"Reply <b>2</b> — Reschedule this meeting\n"
        f"Reply <b>3</b> — I will handle it"
    )

    return await send_telegram_message(message)


# ── Quick test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    async def test():
        print("Step 1: Requesting location from user...")
        await request_user_location()

        print("\nWaiting 20 seconds for you to share location in Telegram...")
        print("👉 Go to Telegram NOW and share your location or type an address")
        await asyncio.sleep(20)

        print("\nStep 2: Reading location from Telegram...")
        location = await get_user_current_location()
        print(f"\n✅ Location received: {location}")

    asyncio.run(test())