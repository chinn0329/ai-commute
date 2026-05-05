import os
from pathlib import Path
from dotenv import load_dotenv
import httpx

load_dotenv(Path(__file__).parent.parent / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


async def send_telegram_message(text: str, chat_id: str = None) -> bool:
    """
    Send a plain text message via Telegram Bot API.
    Uses TELEGRAM_CHAT_ID from .env if chat_id not provided.
    """
    target = chat_id or TELEGRAM_CHAT_ID

    if not target:
        print("[Alert] No TELEGRAM_CHAT_ID set in .env")
        return False

    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": target,
        "text": text,
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


async def send_morning_briefing(day_plan) -> bool:
    """
    Send the morning briefing message with day feasibility score.
    """
    score_pct = int(day_plan.feasibility_score * 100)

    # Emoji based on score
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
        for conflict in day_plan.conflicts[:2]:  # max 2
            lines.append(f"  • {conflict}")
        lines.append("")

    if day_plan.recommendations:
        lines.append("💡 <b>Recommendations:</b>")
        for rec in day_plan.recommendations[:3]:  # max 3
            lines.append(f"  • {rec}")
        lines.append("")

    if day_plan.weather_advisory:
        lines.append(f"🌦 <b>Weather:</b> {day_plan.weather_advisory}")

    message = "\n".join(lines)
    return await send_telegram_message(message)


async def send_departure_alert(
    appointment,
    reasoning_result: dict,
) -> bool:
    """
    Send a departure alert 30 minutes before an appointment.
    """
    mode_emojis = {
        "cab":              "🚕",
        "metro_auto_hybrid":"🚇",
        "bus":              "🚌",
        "walk":             "🚶",
        "metro_bus":        "🚇",
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


async def send_reroute_alert(
    appointment,
    reroute_result: dict,
) -> bool:
    """
    Send an urgent re-route alert when traffic changes significantly.
    """
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


async def send_conflict_alert(
    conflict: str,
    appointment_title: str,
) -> bool:
    """
    Send a conflict detection alert.
    """
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
        print("Testing Telegram Bot...")
        success = await send_telegram_message(
            "🤖 <b>AI-Commute Test Message</b>\n\n"
            "✅ Your Telegram bot is connected and working!\n"
            "You will receive departure alerts and day briefings here.\n\n"
            "<i>— AI-Commute, RVCE Hackathon 2026</i>"
        )
        if success:
            print("✅ Check your Telegram — message should have arrived!")
        else:
            print("❌ Failed — check your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")

    asyncio.run(test())