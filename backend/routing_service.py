import os
import httpx
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from dotenv import load_dotenv

from models import Appointment, RouteOption, UserPreferences

load_dotenv()

TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY")

IST = timezone(timedelta(hours=5, minutes=30))

# Bangalore cost estimates (INR)
COST_ESTIMATES = {
    "cab":              {"base": 50, "per_km": 14},
    "auto":             {"base": 30, "per_km": 12},
    "metro":            {"base": 10, "per_km": 2},
    "bus":              {"base": 10, "per_km": 1},
    "walk":             {"base": 0,  "per_km": 0},
    "metro_auto_hybrid":{"base": 40, "per_km": 4},
}


async def geocode_address(address: str) -> Optional[dict]:
    """
    Convert a Bangalore address string into lat/lon using TomTom Search API.
    Returns {"lat": ..., "lon": ...} or None.
    """
    url = "https://api.tomtom.com/search/2/geocode/{query}.json".format(
        query=httpx.URL(address)
    )
    params = {
        "key": TOMTOM_API_KEY,
        "countrySet": "IN",
        "lat": 12.9716,
        "lon": 77.5946,
        "radius": 50000,
        "limit": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://api.tomtom.com/search/2/geocode/{address}.json",
                params=params
            )
            response.raise_for_status()
            data = response.json()

        results = data.get("results", [])
        if not results:
            print(f"[Routing] Could not geocode: {address}")
            return None

        pos = results[0]["position"]
        print(f"[Routing] Geocoded '{address}' → {pos['lat']}, {pos['lon']}")
        return {"lat": pos["lat"], "lon": pos["lon"]}

    except Exception as e:
        print(f"[Routing] Geocoding error for '{address}': {e}")
        return None


async def get_tomtom_route(
    origin_coords: dict,
    dest_coords: dict,
    departure_time: datetime,
    travel_mode: str = "car",
) -> Optional[dict]:
    """
    Call TomTom Routing API for a specific travel mode.
    travel_mode options: car, pedestrian, bicycle, bus
    Returns raw summary dict with duration and distance.
    """
    origin_str = f"{origin_coords['lat']},{origin_coords['lon']}"
    dest_str   = f"{dest_coords['lat']},{dest_coords['lon']}"

    url = f"https://api.tomtom.com/routing/1/calculateRoute/{origin_str}:{dest_str}/json"

    # Format departure time for TomTom (ISO 8601)
    dep_str = departure_time.strftime("%Y-%m-%dT%H:%M:%S")

    params = {
        "key": TOMTOM_API_KEY,
        "travelMode": travel_mode,
        "traffic": "true",
        "departAt": dep_str,
        "routeType": "fastest",
        "language": "en-GB",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        routes = data.get("routes", [])
        if not routes:
            return None

        summary = routes[0]["summary"]
        legs    = routes[0].get("legs", [{}])

        # Build step instructions from leg points
        steps = []
        for leg in legs:
            for point in leg.get("points", [])[:3]:
                steps.append(f"Continue towards destination")
            break

        return {
            "duration_seconds": summary["travelTimeInSeconds"],
            "distance_meters":  summary["lengthInMeters"],
            "traffic_delay_s":  summary.get("trafficDelayInSeconds", 0),
            "steps": steps or ["Head towards destination"],
        }

    except Exception as e:
        print(f"[Routing] TomTom API error (mode={travel_mode}): {e}")
        return None


async def get_route_options(
    origin: str,
    destination: str,
    departure_time: datetime,
    preferences: Optional[UserPreferences] = None,
    weather_avoid_modes: Optional[list] = None,
) -> List[RouteOption]:
    """
    Main function — fetch all route options between two Bangalore locations.
    Returns a ranked list of RouteOption objects.
    """
    weather_avoid_modes = weather_avoid_modes or []
    routes = []

    # Step 1: Geocode both addresses
    origin_coords = await geocode_address(origin)
    dest_coords   = await geocode_address(destination)

    if not origin_coords or not dest_coords:
        print(f"[Routing] Failed to geocode addresses. Cannot fetch routes.")
        return []

    # Step 2: Get distance for reference
    distance_m  = haversine_distance(origin_coords, dest_coords)
    distance_km = distance_m / 1000

    # Step 3: Fetch cab route (always)
    car_data = await get_tomtom_route(
        origin_coords, dest_coords, departure_time, travel_mode="car"
    )
    if car_data:
        duration_min = car_data["duration_seconds"] // 60
        dist_km = car_data["distance_meters"] / 1000
        routes.append(RouteOption(
            mode="cab",
            duration_minutes=duration_min,
            distance_km=round(dist_km, 2),
            cost_inr=estimate_cost("cab", dist_km),
            steps=["Book Ola/Uber from current location", "Head to destination via fastest route"],
            departure_time=departure_time,
            arrival_time=departure_time + timedelta(seconds=car_data["duration_seconds"]),
        ))
        traffic_delay = car_data["traffic_delay_s"] // 60
        if traffic_delay > 5:
            print(f"[Routing] ⚠ Traffic delay on cab route: +{traffic_delay} min")

    # Step 4: Walking route (only under 3km)
    if distance_km <= 3.0 and "walk" not in weather_avoid_modes:
        walk_data = await get_tomtom_route(
            origin_coords, dest_coords, departure_time, travel_mode="pedestrian"
        )
        if walk_data:
            duration_min = walk_data["duration_seconds"] // 60
            routes.append(RouteOption(
                mode="walk",
                duration_minutes=duration_min,
                distance_km=round(walk_data["distance_meters"] / 1000, 2),
                cost_inr=0,
                steps=["Walk to destination"],
                departure_time=departure_time,
                arrival_time=departure_time + timedelta(seconds=walk_data["duration_seconds"]),
            ))

    # Step 5: Build Metro + Auto hybrid (estimated)
    # Based on car duration — metro avoids traffic
    if car_data and "metro" not in weather_avoid_modes:
        metro_duration = estimate_metro_duration(distance_km, car_data)
        metro_cost     = estimate_cost("metro", distance_km * 0.7) + estimate_cost("auto", distance_km * 0.3)
        routes.append(RouteOption(
            mode="metro_auto_hybrid",
            duration_minutes=metro_duration,
            distance_km=round(distance_km, 2),
            cost_inr=int(metro_cost),
            steps=[
                "Walk to nearest Namma Metro station (~7 min)",
                "Take Metro towards destination station",
                "Auto rickshaw for last mile to destination",
            ],
            departure_time=departure_time,
            arrival_time=departure_time + timedelta(minutes=metro_duration),
        ))

    # Step 6: BMTC Bus estimate
    if car_data and "bus" not in weather_avoid_modes:
        bus_duration = int(car_data["duration_seconds"] / 60 * 1.2)  # 20% slower than car
        bus_cost     = estimate_cost("bus", distance_km)
        routes.append(RouteOption(
            mode="bus",
            duration_minutes=bus_duration,
            distance_km=round(distance_km, 2),
            cost_inr=bus_cost,
            steps=[
                "Walk to nearest BMTC bus stop",
                "Take BMTC bus towards destination",
                "Alight and walk to final destination",
            ],
            departure_time=departure_time,
            arrival_time=departure_time + timedelta(minutes=bus_duration),
        ))

    # Step 7: Apply preference and weather filters
    if preferences:
        routes = apply_preference_filter(routes, preferences, weather_avoid_modes)

    # Step 8: Sort by optimization
    optimization = preferences.optimization if preferences else "fastest"
    routes = sort_routes(routes, optimization)

    print(f"\n[Routing] {len(routes)} routes from '{origin}' to '{destination}':")
    for r in routes:
        print(f"  • {r.mode:22s} {r.duration_minutes:3d} min   ₹{r.cost_inr or 0}")

    return routes


def estimate_metro_duration(distance_km: float, car_data: dict) -> int:
    """
    Estimate Metro + Auto duration.
    Metro is faster than car in traffic but has walk + wait overhead.
    """
    car_min         = car_data["duration_seconds"] / 60
    traffic_delay   = car_data["traffic_delay_s"] / 60

    # Metro saves traffic delay but adds 15 min overhead (walk to station + wait)
    overhead        = 15
    metro_travel    = max(car_min - traffic_delay * 0.8, distance_km * 2.5)
    return int(metro_travel + overhead)


def haversine_distance(coord1: dict, coord2: dict) -> float:
    """
    Straight-line distance in meters between two lat/lon points.
    Used as a fallback distance estimate.
    """
    import math
    R    = 6371000  # Earth radius in meters
    lat1 = math.radians(coord1["lat"])
    lat2 = math.radians(coord2["lat"])
    dlat = math.radians(coord2["lat"] - coord1["lat"])
    dlon = math.radians(coord2["lon"] - coord1["lon"])

    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def estimate_cost(mode: str, distance_km: float) -> int:
    cfg  = COST_ESTIMATES.get(mode, COST_ESTIMATES["cab"])
    cost = cfg["base"] + cfg["per_km"] * distance_km
    return max(10, int(cost))


def apply_preference_filter(
    routes: List[RouteOption],
    preferences: UserPreferences,
    weather_avoid: list,
) -> List[RouteOption]:
    avoid = set(weather_avoid)
    if preferences.avoid_cabs:
        avoid.add("cab")
    if preferences.has_luggage:
        avoid.add("walk")
    if preferences.low_battery:
        avoid.add("metro_auto_hybrid")
    if preferences.eco_friendly:
        avoid.add("cab")

    filtered = [r for r in routes if not any(a in r.mode for a in avoid)]
    return filtered if filtered else routes


def sort_routes(routes: List[RouteOption], optimization: str) -> List[RouteOption]:
    if optimization == "fastest":
        return sorted(routes, key=lambda r: r.duration_minutes)
    elif optimization == "cheapest":
        return sorted(routes, key=lambda r: r.cost_inr or 999)
    elif optimization == "least_walking":
        order = {"cab": 0, "metro_auto_hybrid": 1, "bus": 2, "walk": 3}
        return sorted(routes, key=lambda r: order.get(r.mode, 5))
    return routes


def get_best_route(routes: List[RouteOption]) -> Optional[RouteOption]:
    return routes[0] if routes else None


# ── Quick test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    async def test():
        print("Testing TomTom Routing API for Bangalore...\n")
        departure = datetime.now(IST) + timedelta(minutes=30)

        routes = await get_route_options(
            origin="Indiranagar, Bangalore",
            destination="Whitefield, Bangalore",
            departure_time=departure,
        )

        print(f"\n✅ Route options: {len(routes)}")
        for r in routes:
            print(f"\n  Mode     : {r.mode}")
            print(f"  Duration : {r.duration_minutes} min")
            print(f"  Distance : {r.distance_km} km")
            print(f"  Cost     : ₹{r.cost_inr}")
            print(f"  Arrives  : {r.arrival_time.strftime('%I:%M %p')}")

    asyncio.run(test())