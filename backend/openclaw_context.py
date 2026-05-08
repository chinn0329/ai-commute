"""
openclaw_context.py

Implements OpenClaw's context assembly pipeline pattern.
Pulls all data sources simultaneously, normalises into a single
structured context object, and injects into Groq reasoning.

This follows OpenClaw's architecture:
- Named connectors for each data source
- Unified context object assembly
- Structured JSON context fed to LLM system prompt
- Event-trigger system for conflict detection
- State persistence across reasoning cycles
"""

import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

IST = timezone(timedelta(hours=5, minutes=30))

# ── OpenClaw context state (persisted across cycles) ─────────────
_context_state = {
    "last_assembled": None,
    "last_location":  None,
    "cycle_count":    0,
    "conflicts_seen": [],
}


# ════════════════════════════════════════════════════════════════
# OPENCLAW CONNECTOR: Google Calendar
# ════════════════════════════════════════════════════════════════
async def connector_google_calendar() -> dict:
    """
    OpenClaw named connector: google_calendar
    Pulls today's appointments and normalises into context schema.
    """
    try:
        from calendar_service import get_todays_appointments
        appointments = get_todays_appointments()

        return {
            "connector": "google_calendar",
            "status":    "ok",
            "data": [
                {
                    "id":          a.id,
                    "title":       a.title,
                    "location":    a.location,
                    "start_time":  a.start_time.isoformat(),
                    "end_time":    a.end_time.isoformat(),
                    "start_label": a.start_time.strftime("%I:%M %p"),
                    "end_label":   a.end_time.strftime("%I:%M %p"),
                    "duration_minutes": int(
                        (a.end_time - a.start_time).total_seconds() / 60
                    ),
                }
                for a in appointments
            ],
            "count": len(appointments),
        }
    except Exception as e:
        return {"connector": "google_calendar", "status": "error", "error": str(e), "data": []}


# ════════════════════════════════════════════════════════════════
# OPENCLAW CONNECTOR: TomTom Traffic
# ════════════════════════════════════════════════════════════════
async def connector_tomtom_traffic(
    appointments: list,
    user_location: str,
    preferences=None,
) -> dict:
    """
    OpenClaw named connector: tomtom_traffic
    Fetches routes for all appointments from user location.
    Normalises into unified context schema.
    """
    try:
        from routing_service   import get_route_options
        from weather_service   import get_weather_mode_overrides

        now = datetime.now(IST)
        all_routes = {}

        for appt in appointments:
            from models import Appointment
            appt_obj = Appointment(
                id=appt["id"],
                title=appt["title"],
                location=appt["location"],
                start_time=datetime.fromisoformat(appt["start_time"]),
                end_time=datetime.fromisoformat(appt["end_time"]),
            )

            departure = appt_obj.start_time - timedelta(hours=1)
            if departure < now:
                departure = now + timedelta(minutes=5)

            routes = await get_route_options(
                origin=user_location,
                destination=appt["location"],
                departure_time=departure,
                preferences=preferences,
            )

            all_routes[appt["id"]] = [
                {
                    "mode":             r.mode,
                    "duration_minutes": r.duration_minutes,
                    "distance_km":      r.distance_km,
                    "cost_inr":         r.cost_inr,
                    "departure_time":   r.departure_time.isoformat(),
                    "arrival_time":     r.arrival_time.isoformat(),
                    "departure_label":  r.departure_time.strftime("%I:%M %p"),
                    "arrival_label":    r.arrival_time.strftime("%I:%M %p"),
                    "steps":            r.steps,
                }
                for r in routes
            ]

        return {
            "connector": "tomtom_traffic",
            "status":    "ok",
            "data":       all_routes,
            "user_location": user_location,
        }

    except Exception as e:
        return {
            "connector": "tomtom_traffic",
            "status": "error",
            "error":  str(e),
            "data":   {},
        }


# ════════════════════════════════════════════════════════════════
# OPENCLAW CONNECTOR: OpenWeatherMap
# ════════════════════════════════════════════════════════════════
async def connector_openweathermap() -> dict:
    """
    OpenClaw named connector: openweathermap
    Fetches Bangalore weather and normalises into context schema.
    """
    try:
        from weather_service import get_bangalore_weather, get_weather_mode_overrides
        weather = await get_bangalore_weather()
        avoid   = get_weather_mode_overrides(weather)

        return {
            "connector":       "openweathermap",
            "status":          "ok",
            "data": {
                "temperature_c": weather["temperature_c"],
                "description":   weather["description"],
                "is_raining":    weather["is_raining"],
                "is_heavy_rain": weather["is_heavy_rain"],
                "advisory":      weather["advisory"],
                "avoid_modes":   avoid,
            },
        }
    except Exception as e:
        return {
            "connector": "openweathermap",
            "status":    "error",
            "error":     str(e),
            "data": {
                "temperature_c": 28,
                "description":   "unknown",
                "is_raining":    False,
                "avoid_modes":   [],
            },
        }


# ════════════════════════════════════════════════════════════════
# OPENCLAW CONNECTOR: User Preferences + State
# ════════════════════════════════════════════════════════════════
async def connector_user_preferences(preferences=None) -> dict:
    """
    OpenClaw named connector: user_preferences
    Normalises user preference state into context schema.
    """
    from models import UserPreferences

    prefs = preferences or UserPreferences(
        user_id="default",
        allowed_modes=["metro", "bus", "auto", "cab", "walk"],
        optimization="fastest",
        avoid_cabs=False,
        eco_friendly=False,
        has_luggage=False,
        low_battery=False,
    )

    return {
        "connector": "user_preferences",
        "status":    "ok",
        "data": {
            "allowed_modes":  prefs.allowed_modes,
            "optimization":   prefs.optimization,
            "avoid_cabs":     prefs.avoid_cabs,
            "eco_friendly":   prefs.eco_friendly,
            "has_luggage":    prefs.has_luggage,
            "low_battery":    prefs.low_battery,
        },
    }


# ════════════════════════════════════════════════════════════════
# OPENCLAW CONFLICT DETECTION FEED
# ════════════════════════════════════════════════════════════════
def detect_conflicts(appointments: list, routes: dict) -> list:
    """
    OpenClaw event-trigger system.
    Monitors context object for constraint violations.
    Emits conflict events when available transit time
    between appointments falls below computed ETA.
    """
    conflicts = []

    for i in range(len(appointments) - 1):
        current = appointments[i]
        next_appt = appointments[i + 1]

        current_end   = datetime.fromisoformat(current["end_time"])
        next_start    = datetime.fromisoformat(next_appt["start_time"])
        available_min = int((next_start - current_end).total_seconds() / 60)

        # Get fastest route for next appointment
        next_routes = routes.get(next_appt["id"], [])
        if not next_routes:
            continue

        fastest_min = min(r["duration_minutes"] for r in next_routes)
        buffer_min  = available_min - fastest_min

        if buffer_min < 0:
            conflicts.append({
                "type":          "impossible_transition",
                "severity":      "high",
                "from_meeting":  current["title"],
                "to_meeting":    next_appt["title"],
                "available_min": available_min,
                "required_min":  fastest_min,
                "deficit_min":   abs(buffer_min),
                "message": (
                    f"'{current['title']}' ends at {current['end_time'][11:16]} "
                    f"but '{next_appt['title']}' starts at {next_appt['start_time'][11:16]} "
                    f"— only {available_min} min gap but travel takes {fastest_min} min."
                ),
            })
        elif buffer_min < 15:
            conflicts.append({
                "type":          "tight_transition",
                "severity":      "medium",
                "from_meeting":  current["title"],
                "to_meeting":    next_appt["title"],
                "available_min": available_min,
                "required_min":  fastest_min,
                "buffer_min":    buffer_min,
                "message": (
                    f"Tight transition: only {buffer_min} min buffer "
                    f"between '{current['title']}' and '{next_appt['title']}'."
                ),
            })

    return conflicts


# ════════════════════════════════════════════════════════════════
# OPENCLAW CONTEXT ASSEMBLY PIPELINE — MAIN FUNCTION
# ════════════════════════════════════════════════════════════════
async def assemble_context(
    user_location: str,
    preferences=None,
) -> dict:
    """
    OpenClaw context assembly pipeline.

    Pulls all named connectors simultaneously (parallel fetch),
    normalises into a single structured context object,
    runs conflict detection feed,
    and returns the unified context ready for Groq injection.

    This is the core OpenClaw pattern:
    heterogeneous data sources → unified queryable context object.
    """
    global _context_state

    now = datetime.now(IST)
    print(f"\n[OpenClaw] Assembling context at {now.strftime('%I:%M %p')}")
    print(f"[OpenClaw] User location: {user_location}")

    # ── Step 1: Pull all connectors simultaneously ────────────────
    print("[OpenClaw] Pulling all connectors in parallel...")

    calendar_task    = connector_google_calendar()
    weather_task     = connector_openweathermap()
    preferences_task = connector_user_preferences(preferences)

    # Run calendar and weather in parallel first
    calendar_result, weather_result, prefs_result = await asyncio.gather(
        calendar_task,
        weather_task,
        preferences_task,
    )

    appointments = calendar_result.get("data", [])

    # ── Step 2: Fetch routes (needs appointments first) ───────────
    traffic_result = await connector_tomtom_traffic(
        appointments, user_location, preferences
    )

    routes = traffic_result.get("data", {})

    print(f"[OpenClaw] Connectors done:")
    print(f"  calendar     → {calendar_result['status']} ({len(appointments)} appointments)")
    print(f"  weather      → {weather_result['status']} ({weather_result['data'].get('description', 'N/A')})")
    print(f"  tomtom       → {traffic_result['status']} ({len(routes)} routes fetched)")
    print(f"  preferences  → {prefs_result['status']}")

    # ── Step 3: Run conflict detection feed ───────────────────────
    conflicts = detect_conflicts(appointments, routes)
    print(f"[OpenClaw] Conflicts detected: {len(conflicts)}")

    # ── Step 4: Compute feasibility score ─────────────────────────
    if not appointments:
        feasibility = 1.0
    else:
        high_conflicts   = sum(1 for c in conflicts if c["severity"] == "high")
        medium_conflicts = sum(1 for c in conflicts if c["severity"] == "medium")
        weather_penalty  = 0.1 if weather_result["data"]["is_raining"] else 0
        feasibility = max(
            0.2,
            1.0
            - (high_conflicts * 0.25)
            - (medium_conflicts * 0.10)
            - weather_penalty,
        )

    # ── Step 5: Assemble unified context object ───────────────────
    context = {
        "meta": {
            "assembled_at":     now.isoformat(),
            "assembled_at_label": now.strftime("%I:%M %p IST"),
            "user_location":    user_location,
            "cycle":            _context_state["cycle_count"] + 1,
            "openclaw_version": "2026.3.20",
            "skill":            "commuteiq",
        },
        "connectors": {
            "google_calendar":  calendar_result,
            "tomtom_traffic":   traffic_result,
            "openweathermap":   weather_result,
            "user_preferences": prefs_result,
        },
        "appointments":     appointments,
        "routes":           routes,
        "conflicts":        conflicts,
        "feasibility_score": round(feasibility, 2),
        "weather": weather_result["data"],
        "preferences": prefs_result["data"],
    }

    # ── Step 6: Update state ──────────────────────────────────────
    _context_state["last_assembled"] = now.isoformat()
    _context_state["last_location"]  = user_location
    _context_state["cycle_count"]   += 1
    _context_state["conflicts_seen"] = [c["message"] for c in conflicts]

    print(f"[OpenClaw] Context assembled successfully ✅")
    print(f"  Feasibility : {int(feasibility * 100)}%")
    print(f"  Appointments: {len(appointments)}")
    print(f"  Conflicts   : {len(conflicts)}")

    return context


# ════════════════════════════════════════════════════════════════
# INJECT CONTEXT INTO GROQ SYSTEM PROMPT
# ════════════════════════════════════════════════════════════════
def build_groq_system_prompt(context: dict) -> str:
    """
    Takes the assembled OpenClaw context object and builds
    the Groq system prompt with full world-state injected.
    This ensures Groq always reasons over complete,
    consistent context — not fragmented inputs.
    """
    appointments = context["appointments"]
    weather      = context["weather"]
    prefs        = context["preferences"]
    conflicts    = context["conflicts"]
    feasibility  = context["feasibility_score"]

    appt_lines = []
    for a in appointments:
        appt_lines.append(
            f"  - '{a['title']}' at {a['location']} | {a['start_label']} - {a['end_label']}"
        )

    routes_lines = []
    for appt_id, route_list in context["routes"].items():
        appt = next((a for a in appointments if a["id"] == appt_id), None)
        if appt:
            routes_lines.append(f"  Routes for '{appt['title']}':")
            for r in route_list[:3]:
                routes_lines.append(
                    f"    • {r['mode']}: {r['duration_minutes']}min, "
                    f"₹{r['cost_inr']}, arrives {r['arrival_label']}"
                )

    conflict_lines = [f"  - {c['message']}" for c in conflicts]

    prompt = f"""You are AI-Commute, an autonomous day logistics agent for Bangalore professionals.
You are powered by the OpenClaw context framework which has assembled the following
complete world-state for you to reason over.

=== OPENCLAW CONTEXT OBJECT ===
Assembled at : {context['meta']['assembled_at_label']}
User location: {context['meta']['user_location']}
Skill        : {context['meta']['skill']} v{context['meta']['openclaw_version']}

APPOINTMENTS ({len(appointments)} today):
{chr(10).join(appt_lines) if appt_lines else '  None today'}

AVAILABLE ROUTES:
{chr(10).join(routes_lines) if routes_lines else '  No routes computed'}

CONFLICTS DETECTED ({len(conflicts)}):
{chr(10).join(conflict_lines) if conflict_lines else '  None'}

DAY FEASIBILITY SCORE: {int(feasibility * 100)}%

WEATHER (Bangalore):
  {weather['description']}, {weather['temperature_c']}°C
  Raining: {weather['is_raining']}
  Advisory: {weather.get('advisory') or 'None'}
  Avoid modes: {weather.get('avoid_modes', [])}

USER PREFERENCES:
  Optimization : {prefs['optimization']}
  Avoid cabs   : {prefs['avoid_cabs']}
  Eco-friendly : {prefs['eco_friendly']}
  Has luggage  : {prefs['has_luggage']}
  Low battery  : {prefs['low_battery']}

BANGALORE CONTEXT:
  - Silk Board and ORR are heavily congested 8-10 AM and 5-8 PM
  - Namma Metro Purple Line: Whitefield ↔ Chalukya
  - Namma Metro Green Line: Nagasandra ↔ Silk Institute
  - Peak hour cab surge: 1.5-2.5x normal price
  - Auto rickshaws best for last mile under 5km
=== END OPENCLAW CONTEXT ===

Reason over the COMPLETE context above. Never ask for more information —
everything you need is in the context object.
"""
    return prompt


def get_context_state() -> dict:
    """Return current OpenClaw context state for debugging."""
    return _context_state


# ── Quick test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    async def test():
        print("Testing OpenClaw context assembly pipeline...\n")

        context = await assemble_context(
            user_location="Indiranagar, Bangalore",
        )

        print("\n" + "="*50)
        print("ASSEMBLED CONTEXT OBJECT:")
        print("="*50)
        print(f"Meta         : {context['meta']}")
        print(f"Appointments : {len(context['appointments'])}")
        print(f"Routes       : {len(context['routes'])} appointment routes")
        print(f"Conflicts    : {len(context['conflicts'])}")
        print(f"Feasibility  : {int(context['feasibility_score']*100)}%")
        print(f"Weather      : {context['weather']['description']}")

        print("\n" + "="*50)
        print("GROQ SYSTEM PROMPT PREVIEW:")
        print("="*50)
        prompt = build_groq_system_prompt(context)
        print(prompt[:500] + "...")

    asyncio.run(test())