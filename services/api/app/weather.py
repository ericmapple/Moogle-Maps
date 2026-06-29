from __future__ import annotations

from typing import Any

import httpx

from app.geo import ORIGIN

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherError(RuntimeError):
    """Raised when the weather provider cannot be reached."""


async def fetch_weather() -> dict[str, Any]:
    params = {
        "latitude": ORIGIN["latitude"],
        "longitude": ORIGIN["longitude"],
        "current": ",".join(
            [
                "temperature_2m",
                "apparent_temperature",
                "precipitation",
                "rain",
                "snowfall",
                "weather_code",
                "wind_speed_10m",
                "wind_gusts_10m",
            ]
        ),
        "hourly": ",".join(
            [
                "temperature_2m",
                "precipitation_probability",
                "weather_code",
                "wind_speed_10m",
            ]
        ),
        "forecast_hours": 6,
        "timezone": "America/Toronto",
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(OPEN_METEO_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise WeatherError("Open-Meteo weather data is temporarily unavailable") from exc

    current = payload["current"]
    hourly = payload["hourly"]

    return {
        "source": "open-meteo",
        "origin": ORIGIN,
        "timezone": payload.get("timezone"),
        "current": {
            "time": current.get("time"),
            "temperatureC": current.get("temperature_2m"),
            "apparentTemperatureC": current.get("apparent_temperature"),
            "precipitationMm": current.get("precipitation"),
            "rainMm": current.get("rain"),
            "snowfallCm": current.get("snowfall"),
            "weatherCode": current.get("weather_code"),
            "condition": weather_condition(current.get("weather_code")),
            "windSpeedKmh": current.get("wind_speed_10m"),
            "windGustsKmh": current.get("wind_gusts_10m"),
        },
        "hourly": [
            {
                "time": time,
                "temperatureC": hourly["temperature_2m"][index],
                "precipitationProbability": hourly["precipitation_probability"][index],
                "weatherCode": hourly["weather_code"][index],
                "condition": weather_condition(hourly["weather_code"][index]),
                "windSpeedKmh": hourly["wind_speed_10m"][index],
            }
            for index, time in enumerate(hourly.get("time", []))
        ],
    }


def weather_condition(code: int | None) -> str:
    if code is None:
        return "Unknown"

    if code == 0:
        return "Clear"
    if code in {1, 2, 3}:
        return "Partly cloudy"
    if code in {45, 48}:
        return "Fog"
    if code in {51, 53, 55, 56, 57}:
        return "Drizzle"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "Rain"
    if code in {71, 73, 75, 77, 85, 86}:
        return "Snow"
    if code in {95, 96, 99}:
        return "Thunderstorm"

    return "Mixed"
