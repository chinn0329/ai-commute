import os
import httpx
from dotenv import load_dotenv

load_dotenv()

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

# Bangalore coordinates
BANGALORE_LAT = 12.9716
BANGALORE_LON = 77.5946

async def get_bangalore_weather() -> dict:
    """
    Fetch current weather for Bangalore.
    Returns a dict with rain status, temperature, and advisory.
    """
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": BANGALORE_LAT,
        "lon": BANGALORE_LON,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    weather_id = data["weather"][0]["id"]
    description = data["weather"][0]["description"]
    temp = data["main"]["temp"]
    rain = data.get("rain", {}).get("1h", 0)

    # Weather condition flags
    is_raining = weather_id >= 200 and weather_id < 700
    is_heavy_rain = rain > 5
    is_extreme = weather_id < 300 or (weather_id >= 900 and weather_id < 910)

    # Build advisory
    advisory = None
    if is_heavy_rain:
        advisory = "Heavy rain in Bangalore — avoid bike and walking-heavy routes. Allow extra 20 min buffer."
    elif is_raining:
        advisory = "Light rain in Bangalore — avoid bike routes. Consider Metro or cab."
    elif is_extreme:
        advisory = "Extreme weather conditions — check before leaving."

    result = {
        "temperature_c": round(temp, 1),
        "description": description,
        "is_raining": is_raining,
        "is_heavy_rain": is_heavy_rain,
        "is_extreme": is_extreme,
        "advisory": advisory,
        "raw": data,
    }

    print(f"[Weather] {description}, {temp}°C — Rain: {is_raining}")
    if advisory:
        print(f"[Weather] Advisory: {advisory}")

    return result


def get_weather_mode_overrides(weather: dict) -> list:
    """
    Return a list of transport modes to AVOID based on weather.
    """
    avoid = []
    if weather["is_raining"]:
        avoid.append("bike")
        avoid.append("walk")
    if weather["is_heavy_rain"]:
        avoid.append("auto")
    return avoid


# ── Quick test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    async def test():
        print("Testing OpenWeatherMap for Bangalore...")
        weather = await get_bangalore_weather()
        print(f"\n✅ Weather fetched:")
        print(f"   Temperature : {weather['temperature_c']}°C")
        print(f"   Condition   : {weather['description']}")
        print(f"   Raining     : {weather['is_raining']}")
        print(f"   Advisory    : {weather['advisory'] or 'None'}")
        overrides = get_weather_mode_overrides(weather)
        print(f"   Avoid modes : {overrides or 'None'}")
    asyncio.run(test())