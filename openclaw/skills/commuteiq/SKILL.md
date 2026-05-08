# CommuteIQ Skill

## Description
AI-powered day logistics agent for Bangalore professionals.
Reads Google Calendar, plans multi-modal routes, detects scheduling
conflicts, and sends proactive Telegram alerts.

## Triggers
- Morning analysis at 5:00 AM daily
- "plan my day" / "commute" / "route" / "traffic"
- "am I late" / "will I make it" / "best route to"
- Location share from user
- Numbered replies: 1, 2, 3 (conflict resolution)

## Capabilities
- Read Google Calendar appointments
- Fetch live Bangalore traffic via TomTom API
- Compute multi-modal routes (Metro + Auto + Bus + Cab + Walk)
- Detect cascading scheduling conflicts across full day
- Score day feasibility (0-100%)
- Send proactive departure alerts 30 min before each appointment
- Re-route every 10 minutes when traffic changes
- Handle weather-based mode switching
- Two-way reply handling for conflict resolution

## Context Sources
- google_calendar: appointments, locations, times
- tomtom_traffic: live traffic, ETAs, delays
- openweathermap: rain, temperature, weather advisory
- user_preferences: transport mode, optimization priority
- user_location: GPS coordinates or text address from Telegram

## Output Channels
- Telegram: morning briefing, departure alerts, conflict alerts, reroute alerts

## API Endpoint
POST http://localhost:8000/analyse
GET  http://localhost:8000/appointments
GET  http://localhost:8000/routes
GET  http://localhost:8000/weather

## Setup
See README.md for full setup instructions.
Requires: GROQ_API_KEY, TOMTOM_API_KEY, OPENWEATHER_API_KEY,
          TELEGRAM_BOT_TOKEN, GOOGLE credentials