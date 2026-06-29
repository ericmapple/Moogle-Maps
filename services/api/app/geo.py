from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any

ORIGIN: dict[str, Any] = {
    "id": "concordia-hall",
    "name": "Concordia University - Henry F. Hall Building",
    "address": "1455 De Maisonneuve Blvd. W., Montreal",
    "latitude": 45.4971,
    "longitude": -73.5788,
}

WALKING_SPEED_METERS_PER_MINUTE = 80
BIKING_SPEED_METERS_PER_MINUTE = 240


def distance_meters(
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
) -> int:
    radius_meters = 6_371_000
    lat_delta = radians(lat_b - lat_a)
    lon_delta = radians(lon_b - lon_a)
    start_lat = radians(lat_a)
    end_lat = radians(lat_b)

    haversine = (
        sin(lat_delta / 2) ** 2
        + cos(start_lat) * cos(end_lat) * sin(lon_delta / 2) ** 2
    )
    return round(2 * radius_meters * asin(sqrt(haversine)))


def travel_estimates_from_origin(latitude: float, longitude: float) -> dict[str, int]:
    meters = distance_meters(
        ORIGIN["latitude"],
        ORIGIN["longitude"],
        latitude,
        longitude,
    )

    return {
        "distanceFromOriginMeters": meters,
        "walkMinutes": max(1, round(meters / WALKING_SPEED_METERS_PER_MINUTE)),
        "bikeMinutes": max(1, round(meters / BIKING_SPEED_METERS_PER_MINUTE)),
    }
