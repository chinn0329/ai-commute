import os
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from groq import Groq
from dotenv import load_dotenv

from models import Appointment, RouteOption, DayPlan, UserPreferences

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
IST = timezone(timedelta(hours=5, minutes=30))

client = Groq(api_key=GROQ_API_KEY)


# ── Helper: format appointments for prompt ────────────────────────
def format_appointments(appointments: List[Appointment]) -> str:
    if not appointments:
        return "No appointments today."
    lines = []
    for i, appt in enumerate(appointments, 1):
        start = appt.start_time.strftime("%I:%M %p")
        end   = appt.end_time.strftime("%I:%M %p")
        lines.append(
            f"{i}. '{appt.title}' at {appt.location} | {start} - {end}"
        )
    return "\n".join(lines)


# ── Helper: format routes for prompt ─────────────────────────────
def format_routes(routes: List[RouteOption]) -> str:
    if not routes:
        return "No routes available."
    lines = []
    for r in routes:
        lines.append(
            f"  - {r.mode}: {r.duration_minutes} min, "
            f"₹{r.cost_inr or 0}, arrives {r.arrival_time.strftime('%I:%M %p')}"
        )
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 1. SINGLE APPOINTMENT REASONING
# ════════════════════════════════════════════════════════════════
async def reason_single_appointment(
    appointment: Appointment,
    routes: List[RouteOption],
    weather: dict,
    preferences: Optional[UserPreferences] = None,
    current_location: str = "your current location",
) -> dict:
    """
    For one appointment, pick the best route and explain it clearly.
    Returns a dict with recommendation, departure_time, and alert_message.
    """
    pref_text = "No specific preferences set."
    if preferences:
        pref_text = (
            f"Optimization: {preferences.optimization}. "
            f"Avoid cabs: {preferences.avoid_cabs}. "
            f"Has luggage: {preferences.has_luggage}. "
            f"Eco-friendly: {preferences.eco_friendly}."
        )

    weather_text = (
        f"{weather['description']}, {weather['temperature_c']}°C. "
        f"Raining: {weather['is_raining']}. "
        f"Advisory: {weather.get('advisory') or 'None'}."
    )

    prompt = f"""You are AI-Commute, a smart day logistics assistant for Bangalore professionals.

APPOINTMENT:
'{appointment.title}' at {appointment.location}
Starts: {appointment.start_time.strftime('%I:%M %p')}

AVAILABLE ROUTES from {current_location}:
{format_routes(routes)}

CURRENT BANGALORE WEATHER:
{weather_text}

USER PREFERENCES:
{pref_text}

YOUR TASK:
1. Pick the single best route considering all factors
2. Calculate what time the user must leave NOW
3. Write a short, friendly Telegram alert message (max 5 lines)
4. Mention specific Bangalore landmarks/routes where relevant (Silk Board, ORR, Namma Metro, etc.)

Respond in this exact JSON format:
{{
  "best_mode": "cab or metro_auto_hybrid or bus or walk",
  "duration_minutes": 45,
  "cost_inr": 120,
  "departure_time": "09:15 AM",
  "alert_message": "Your friendly alert here with route details",
  "reasoning": "One sentence explanation of why this route was chosen"
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=500,
    )

    raw = response.choices[0].message.content.strip()

    # Parse JSON from response
    try:
        # Extract JSON if wrapped in markdown code block
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        print(f"[Reasoning] Best route for '{appointment.title}': {result['best_mode']} ({result['duration_minutes']} min)")
        return result
    except Exception as e:
        print(f"[Reasoning] JSON parse error: {e}\nRaw: {raw}")
        # Fallback
        best = routes[0] if routes else None
        return {
            "best_mode": best.mode if best else "cab",
            "duration_minutes": best.duration_minutes if best else 45,
            "cost_inr": best.cost_inr if best else 200,
            "departure_time": "Check manually",
            "alert_message": f"Reminder: '{appointment.title}' at {appointment.location} starts at {appointment.start_time.strftime('%I:%M %p')}.",
            "reasoning": "Could not parse AI response — showing best available route.",
        }


# ════════════════════════════════════════════════════════════════
# 2. FULL DAY LOGISTICS REASONING
# ════════════════════════════════════════════════════════════════
async def reason_full_day(
    appointments: List[Appointment],
    all_routes: dict,
    weather: dict,
    preferences: Optional[UserPreferences] = None,
) -> DayPlan:
    """
    Reason across ALL appointments for the day as a connected logistics chain.
    Detects conflicts, scores feasibility, recommends buffers.
    Returns a DayPlan object.
    """
    if not appointments:
        return DayPlan(
            appointments=[],
            feasibility_score=1.0,
            conflicts=[],
            recommendations=["No appointments today. Enjoy your day!"],
        )

    # Build appointment summary for prompt
    appt_text = format_appointments(appointments)

    # Build routes summary
    routes_text = ""
    for appt in appointments:
        appt_routes = all_routes.get(appt.id, [])
        routes_text += f"\nRoutes for '{appt.title}':\n{format_routes(appt_routes)}"

    weather_text = (
        f"{weather['description']}, {weather['temperature_c']}°C. "
        f"Raining: {weather['is_raining']}. "
        f"Advisory: {weather.get('advisory') or 'None'}."
    )

    pref_text = "No specific preferences."
    if preferences:
        pref_text = f"Optimization: {preferences.optimization}. Avoid cabs: {preferences.avoid_cabs}."

    prompt = f"""You are AI-Commute, a smart day logistics assistant for Bangalore professionals.
Analyse the user's ENTIRE workday as a connected logistics chain.

TODAY'S APPOINTMENTS:
{appt_text}

AVAILABLE ROUTES:
{routes_text}

BANGALORE WEATHER TODAY:
{weather_text}

USER PREFERENCES:
{pref_text}

YOUR TASK — analyse the full day and identify:
1. FEASIBILITY SCORE: What % chance does the user have of making all meetings on time? (0.0 to 1.0)
2. CONFLICTS: Which meeting transitions are impossible or risky? (e.g. not enough travel time between meetings)
3. RECOMMENDATIONS: Specific actionable advice (leave early, take metro, add buffer, reschedule)
4. WEATHER ADVISORY: Any weather impact on the day

Important Bangalore context:
- Silk Board junction and ORR are heavily congested during 8-10 AM and 5-8 PM
- Namma Metro Purple Line: Whitefield to Chalukya. Green Line: Nagasandra to Silk Institute
- BMTC buses run on most major routes but are slower during peak hours
- Auto rickshaws are best for last mile (under 5 km)

Respond in this exact JSON format:
{{
  "feasibility_score": 0.75,
  "feasibility_label": "Moderate — 2 meetings at risk",
  "conflicts": [
    "Meeting 2 ends at 12:00 PM but Meeting 3 is at 1:00 PM in Whitefield — only 60 min gap but travel takes 75 min in current traffic",
    "ORR likely congested during your 5 PM departure"
  ],
  "recommendations": [
    "Leave Meeting 2 by 11:45 AM to reach Meeting 3 on time",
    "Consider Metro for the Indiranagar to Whitefield leg — avoids ORR entirely",
    "Add 30 min buffer before your 5 PM Electronic City meeting"
  ],
  "weather_advisory": "Clear weather — all modes available",
  "morning_briefing": "A friendly 4-5 line morning summary message for Telegram/WhatsApp"
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=800,
    )

    raw = response.choices[0].message.content.strip()

    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())

        plan = DayPlan(
            appointments=appointments,
            feasibility_score=result.get("feasibility_score", 0.8),
            conflicts=result.get("conflicts", []),
            recommendations=result.get("recommendations", []),
            weather_advisory=result.get("weather_advisory"),
        )

        print(f"[Reasoning] Day feasibility: {result.get('feasibility_label', 'Unknown')}")
        print(f"[Reasoning] Conflicts found: {len(plan.conflicts)}")
        print(f"[Reasoning] Recommendations: {len(plan.recommendations)}")

        # Attach morning briefing to plan
        plan.morning_briefing = result.get("morning_briefing", "Good morning! Here is your day plan.")
        return plan

    except Exception as e:
        print(f"[Reasoning] Full day parse error: {e}\nRaw: {raw}")
        return DayPlan(
            appointments=appointments,
            feasibility_score=0.7,
            conflicts=["Could not analyse conflicts — check your schedule manually"],
            recommendations=["Leave 30 min early for each appointment to be safe"],
            weather_advisory=weather.get("advisory"),
        )


# ════════════════════════════════════════════════════════════════
# 3. DYNAMIC RE-ROUTING REASONING
# ════════════════════════════════════════════════════════════════
async def reason_reroute(
    appointment: Appointment,
    original_route: RouteOption,
    new_routes: List[RouteOption],
    delay_minutes: int,
    weather: dict,
) -> dict:
    """
    Called when traffic re-check shows ETA has shifted significantly.
    Decides if re-routing is needed and what to tell the user.
    """
    prompt = f"""You are AI-Commute. The user is currently commuting to a meeting.

MEETING: '{appointment.title}' at {appointment.location}
Starts: {appointment.start_time.strftime('%I:%M %p')}

ORIGINAL ROUTE: {original_route.mode} — estimated {original_route.duration_minutes} min
NEW TRAFFIC CHECK: Delay of {delay_minutes} minutes detected on original route.

ALTERNATIVE ROUTES NOW AVAILABLE:
{format_routes(new_routes)}

WEATHER: {weather['description']}, raining: {weather['is_raining']}

Decide if the user should switch routes. If yes, tell them clearly and urgently.
If the delay is minor (under 10 min), reassure them.

Respond in this exact JSON format:
{{
  "action": "reroute or stay",
  "urgency": "high or low",
  "new_mode": "metro_auto_hybrid or cab or bus",
  "message": "Your urgent re-route message here (max 4 lines)",
  "will_be_late": true,
  "minutes_late": 12
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=400,
    )

    raw = response.choices[0].message.content.strip()

    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        print(f"[Reasoning] Re-route decision: {result.get('action')} — urgency: {result.get('urgency')}")
        return result
    except Exception as e:
        print(f"[Reasoning] Re-route parse error: {e}")
        return {
            "action": "reroute",
            "urgency": "high",
            "new_mode": new_routes[0].mode if new_routes else "cab",
            "message": f"⚠️ Traffic delay detected! Consider switching routes to reach '{appointment.title}' on time.",
            "will_be_late": delay_minutes > 15,
            "minutes_late": max(0, delay_minutes - 10),
        }


# ════════════════════════════════════════════════════════════════
# QUICK TEST
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import asyncio

    async def test():
        print("Testing Groq reasoning layer...\n")

        # Mock data
        now = datetime.now(IST)
        appt = Appointment(
            id="test_001",
            title="Client Meeting",
            location="Whitefield, Bangalore",
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(hours=3),
        )

        appt2 = Appointment(
            id="test_002",
            title="Team Standup",
            location="Electronic City, Bangalore",
            start_time=now + timedelta(hours=4),
            end_time=now + timedelta(hours=5),
        )

        routes = [
            RouteOption(
                mode="cab",
                duration_minutes=52,
                distance_km=18.3,
                cost_inr=306,
                steps=["Book cab", "Head via ORR"],
                departure_time=now + timedelta(hours=1),
                arrival_time=now + timedelta(hours=1, minutes=52),
            ),
            RouteOption(
                mode="metro_auto_hybrid",
                duration_minutes=47,
                distance_km=18.3,
                cost_inr=124,
                steps=["Walk to Metro", "Take Purple Line", "Auto last mile"],
                departure_time=now + timedelta(hours=1),
                arrival_time=now + timedelta(hours=1, minutes=47),
            ),
        ]

        weather = {
            "description": "few clouds",
            "temperature_c": 31,
            "is_raining": False,
            "advisory": None,
        }

        # Test 1: Single appointment reasoning
        print("=" * 50)
        print("TEST 1: Single appointment reasoning")
        print("=" * 50)
        result = await reason_single_appointment(appt, routes, weather)
        print(f"\nBest mode    : {result['best_mode']}")
        print(f"Departure    : {result['departure_time']}")
        print(f"Reasoning    : {result['reasoning']}")
        print(f"\nAlert message:\n{result['alert_message']}")

        # Test 2: Full day reasoning
        print("\n" + "=" * 50)
        print("TEST 2: Full day logistics reasoning")
        print("=" * 50)
        all_routes = {"test_001": routes, "test_002": routes}
        plan = await reason_full_day([appt, appt2], all_routes, weather)
        print(f"\nFeasibility  : {plan.feasibility_score}")
        print(f"Conflicts    : {plan.conflicts}")
        print(f"Suggestions  : {plan.recommendations}")
        print(f"\nMorning brief:\n{getattr(plan, 'morning_briefing', 'N/A')}")

    asyncio.run(test())