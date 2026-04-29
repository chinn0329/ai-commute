from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class Appointment(BaseModel):
    id: str
    title: str
    location: str
    start_time: datetime
    end_time: datetime
    description: Optional[str] = None

class RouteOption(BaseModel):
    mode: str                    # metro, bus, cab, walk, hybrid
    duration_minutes: int
    distance_km: float
    cost_inr: Optional[int] = None
    steps: List[str] = []
    departure_time: datetime
    arrival_time: datetime

class DayPlan(BaseModel):
    appointments: List[Appointment]
    feasibility_score: float     # 0.0 to 1.0
    conflicts: List[str] = []
    recommendations: List[str] = []
    weather_advisory: Optional[str] = None

class UserPreferences(BaseModel):
    user_id: str
    allowed_modes: List[str] = ["metro", "bus", "auto", "cab", "walk"]
    optimization: str = "fastest"   # fastest, cheapest, safest, least_walking
    avoid_cabs: bool = False
    eco_friendly: bool = False
    has_luggage: bool = False
    low_battery: bool = False
    telegram_chat_id: Optional[str] = None