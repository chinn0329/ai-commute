# 🚦 AI-Commute

**AI-Powered Day Logistics Agent for Bangalore Professionals**

> PRISM Hackathon 2026 · Theme 2: Daily Utility (Smartphones)  
> Team: Chinmayi C Siddapur, G Tanushri Vaishnavi, Riya Aggarwal, Harshini Jayakumar · RVCE  
> Live API: https://ai-commute-backend.onrender.com

---

## The Problem

Bangalore professionals lose hours every day to avoidable commute chaos:

- **Calendar Blindness** — meetings planned in isolation with no travel-time feasibility check
- **No pre-departure traffic warning** — nobody tells you Silk Board is jammed 30 min before you leave
- **Mode confusion** — cab vs metro vs bus decisions made by guessing, not data
- **Weather ignored** — rain kills walk/auto routes but no tool adapts to live conditions

---

## Our Solution

AI-Commute reads your Google Calendar, fetches live Bangalore traffic, runs Groq AI reasoning across your entire day, and sends intelligent Telegram alerts before every departure — with all route options and costs in the message. It re-checks traffic every 10 minutes and re-routes if conditions change.

```
Google Calendar → TomTom Traffic → OpenClaw Context → Groq AI → Telegram Alert
                                                              ↓
                                               Dynamic re-route every 10 min
```

---

## Features (18 Built)

| # | Feature | Status |
|---|---------|--------|
| 1 | Google Calendar OAuth Integration | Done |
| 2 | Real-Time TomTom Traffic + Routing | Done |
| 3 | Multi-Modal Routes (Cab/Metro/Bus/Walk/Auto) | Done |
| 4 | Full Day Logistics Chain View | Done |
| 5 | Cascading Delay Detection | Done |
| 6 | Day Feasibility Score (morning %) | Done |
| 7 | Dynamic Re-routing Every 10 Minutes | Done |
| 8 | User Mode Preference Engine | Done |
| 9 | Context-Based Adaptive Mode Switching | Done |
| 10 | Weather-Aware Route Adjustment | Done |
| 11 | Two-Way Telegram Interaction (reply 1/2/3) | Done |
| 12 | Intelligent Buffer Recommendations | Done |
| 13 | Cost Comparison Across Modes (INR) | Done |
| 14 | Telegram GPS Location Sharing | Done |
| 15 | Morning Briefing Message (5 AM daily) | Done |
| 16 | Departure Alert (30 min before) | Done |
| 17 | Conflict Alerts with Numbered Reply Options | Done |
| 18 | FastAPI REST Endpoints | Done |

---

## Tech Stack — Rs.0 Total Cost

| Tool | Purpose | Free Limit |
|------|---------|-----------|
| Google Calendar API | Read appointments | 1M req/day |
| TomTom Traffic API | Live Bangalore routing | 2,500 req/day |
| OpenWeatherMap API | Rain detection | 1,000 req/day |
| Groq API (Llama 3.3 70B) | AI full-day reasoning | 30 req/min |
| Telegram Bot API | Alerts + two-way interaction | Unlimited |
| APScheduler | 5AM trigger + 10min re-check | Open source |
| FastAPI + Python | Backend engine | Open source |
| OpenClaw | Context aggregation framework | Open source |
| Render.com | Cloud hosting | Free tier |
| SQLite | Local state storage | Open source |

---

## Architecture

```
+-------------------------------------------------------------+
|                      DATA SOURCES                           |
|  Google Calendar API · TomTom API · OpenWeatherMap API      |
+----------------------------+--------------------------------+
                             |
+----------------------------v--------------------------------+
|                   OPENCLAW CONTEXT LAYER                    |
|  Pulls all APIs simultaneously -> normalises -> JSON context|
+----------------------------+--------------------------------+
                             |
+----------------------------v--------------------------------+
|               GROQ AI REASONING ENGINE                      |
|  Llama 3.3 70B · Full-day plan · Conflict detection        |
|  Feasibility scoring · Re-route decisions                   |
+----------------------------+--------------------------------+
                             |
+----------------------------v--------------------------------+
|                  DELIVERY LAYER                             |
|  FastAPI · APScheduler · Telegram Bot · Render.com          |
+-------------------------------------------------------------+
```

---

## Setup Instructions

### Prerequisites

- Python 3.10+
- A Google account with Calendar API enabled
- Telegram account + a bot created via @BotFather
- Free API keys: TomTom, OpenWeatherMap, Groq

### 1. Clone the repo

```bash
git clone https://github.com/chinn0329/ai-commute.git
cd ai-commute
```

### 2. Create virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac/Linux
python -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 4. Configure environment

Copy `.env.example` to `.env` and fill in all keys:

```env
GROQ_API_KEY=your_groq_key
TOMTOM_API_KEY=your_tomtom_key
OPENWEATHER_API_KEY=your_openweather_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
USER_HOME_LOCATION=Indiranagar, Bangalore
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=http://localhost:8080/
APP_ENV=development
DATABASE_URL=sqlite+aiosqlite:///./aicommute.db
```

### 5. Google Calendar OAuth

```bash
python calendar_service.py
```

Follow the browser prompt to authorise. This creates `token.json`.

### 6. Run locally

```bash
uvicorn main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### 7. Test manually

```bash
python scheduler.py
```

Share your location in Telegram when prompted. You will receive the morning briefing + departure alerts with all route options.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/health` | Status check |
| POST | `/analyse` | Trigger morning analysis |
| GET | `/appointments` | Today's calendar events |
| GET | `/weather` | Bangalore weather |
| GET | `/routes?origin=X&destination=Y` | Route options |
| POST | `/send-test-message` | Test Telegram |
| GET | `/preferences` | Get user preferences |
| POST | `/preferences` | Update preferences |
| GET | `/docs` | Interactive API docs |

Live base URL: `https://ai-commute-backend.onrender.com`

---

## Usage

**5:00 AM** — AI-Commute sends a Telegram message asking for your location. Share GPS pin or type an address. System responds instantly (no 2-hour wait). Morning briefing arrives with conflicts, recommendations, weather.

**30 min before each appointment** — Fresh routes fetched. Departure alert sent to Telegram with all route options and costs. Reply 1 (leaving now), 2 (need more time), or 3 (skip).

**Every 10 minutes while commuting** — Traffic re-checked. If ETA shifts by 15+ min, re-route alert sent with new options.

---

## Demo Scenario — Rohan's Day

Rohan has 4 meetings: 9AM Koramangala, 11AM Whitefield, 2PM Indiranagar, 5PM Electronic City.

- **5:00 AM** — Day scored 61% feasible. Flags Koramangala to Whitefield gap is only 45 min but commute takes 55 min.
- **8:30 AM** — Departure alert: Cab 28min Rs.180 | Bus 35min Rs.25 | Metro+Auto 42min Rs.65
- **9:20 AM** — Accident on ORR detected. New ETA 55 min. Re-route alert sent.
- **9:22 AM** — Rohan replies 1. Switched to BMTC 500C. Reschedule message drafted.

---

## AI Disclosure

**Groq API (Llama 3.3 70B)** — Primary AI engine. Powers full-day logistics reasoning, conflict detection, feasibility scoring, and all Telegram alert content generation.

**OpenClaw** — Context aggregation layer. Pulls live data from all three APIs simultaneously, normalises into structured JSON, and injects into Groq's system prompt.

**Claude (Anthropic)** — Used during development for code generation, debugging, architecture decisions, and writing this documentation. All code was reviewed and validated by the team.

---

## Project Structure

```
ai-commute/
├── backend/
│   ├── main.py
│   ├── calendar_service.py
│   ├── routing_service.py
│   ├── weather_service.py
│   ├── reasoning_service.py
│   ├── alert_service.py
│   ├── scheduler.py
│   ├── models.py
│   ├── auth_check.py
│   └── requirements.txt
├── openclaw/
│   └── skills/commuteiq/
│       ├── SKILL.md
│       └── config.json
├── .env.example
├── render.yaml
└── README.md
```

---

## Team

| Name |
|------|
| Chinmayi C Siddapur |
| G Tanushri Vaishnavi | 
| Riya Aggarwal |
| Harshini Jayakumar |

Institution: RV College of Engineering (RVCE), Bangalore

---

## Links

- GitHub: https://github.com/chinn0329/ai-commute
- Live API: https://ai-commute-backend.onrender.com
- API Docs: https://ai-commute-backend.onrender.com/docs