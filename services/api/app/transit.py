from __future__ import annotations

import csv
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.geo import ORIGIN, distance_meters

STM_GTFS_URL = os.getenv(
    "STM_GTFS_URL",
    "https://www.stm.info/sites/default/files/gtfs/gtfs_stm.zip",
)
DEFAULT_GTFS_PATH = Path(__file__).resolve().parents[3] / "data" / "gtfs" / "gtfs_stm.zip"
MONTREAL_TZ = ZoneInfo("America/Toronto")


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str
    latitude: float
    longitude: float
    distance_meters: int


def gtfs_path() -> Path:
    return Path(os.getenv("STM_GTFS_PATH", str(DEFAULT_GTFS_PATH)))


async def fetch_departures(
    *,
    limit: int = 10,
    radius_meters: int = 650,
    horizon_minutes: int = 90,
) -> dict[str, Any]:
    path = await ensure_gtfs_zip()
    now = datetime.now(MONTREAL_TZ)

    with zipfile.ZipFile(path) as archive:
        stops = _nearby_stops(archive, radius_meters)
        stop_ids = set(stops)
        active_services = _active_service_ids(archive, now)
        routes = _read_routes(archive)
        trips = _read_active_trips(archive, active_services)
        departures = _read_departures(
            archive,
            stops,
            stop_ids,
            routes,
            trips,
            now,
            horizon_minutes,
            limit,
        )

    return {
        "source": "stm-gtfs-static",
        "realtime": False,
        "origin": ORIGIN,
        "radiusMeters": radius_meters,
        "horizonMinutes": horizon_minutes,
        "generatedAt": now.isoformat(),
        "departures": departures,
    }


async def ensure_gtfs_zip() -> Path:
    path = gtfs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cache_hours = int(os.getenv("STM_GTFS_CACHE_HOURS", "12"))

    if path.exists():
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        if age < timedelta(hours=cache_hours):
            return path

    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        response = await client.get(STM_GTFS_URL)
        response.raise_for_status()
        path.write_bytes(response.content)

    return path


def _nearby_stops(archive: zipfile.ZipFile, radius_meters: int) -> dict[str, Stop]:
    stops: dict[str, Stop] = {}
    with archive.open("stops.txt") as file:
        reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
        for row in reader:
            latitude = float(row["stop_lat"])
            longitude = float(row["stop_lon"])
            meters = distance_meters(
                ORIGIN["latitude"],
                ORIGIN["longitude"],
                latitude,
                longitude,
            )
            if meters <= radius_meters:
                stops[row["stop_id"]] = Stop(
                    stop_id=row["stop_id"],
                    name=row["stop_name"],
                    latitude=latitude,
                    longitude=longitude,
                    distance_meters=meters,
                )

    return stops


def _active_service_ids(archive: zipfile.ZipFile, now: datetime) -> set[str]:
    today = now.strftime("%Y%m%d")
    weekday = now.strftime("%A").lower()
    active: set[str] = set()

    with archive.open("calendar.txt") as file:
        reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
        for row in reader:
            if row[weekday] == "1" and row["start_date"] <= today <= row["end_date"]:
                active.add(row["service_id"])

    if "calendar_dates.txt" in archive.namelist():
        with archive.open("calendar_dates.txt") as file:
            reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
            for row in reader:
                if row["date"] != today:
                    continue
                if row["exception_type"] == "1":
                    active.add(row["service_id"])
                if row["exception_type"] == "2":
                    active.discard(row["service_id"])

    return active


def _read_routes(archive: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    with archive.open("routes.txt") as file:
        reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
        return {
            row["route_id"]: {
                "routeId": row["route_id"],
                "shortName": row.get("route_short_name") or row.get("route_long_name") or "",
                "longName": row.get("route_long_name") or "",
                "type": row.get("route_type") or "",
            }
            for row in reader
        }


def _read_active_trips(
    archive: zipfile.ZipFile,
    active_services: set[str],
) -> dict[str, dict[str, str]]:
    with archive.open("trips.txt") as file:
        reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
        return {
            row["trip_id"]: {
                "routeId": row["route_id"],
                "serviceId": row["service_id"],
                "headsign": row.get("trip_headsign") or "",
                "directionId": row.get("direction_id") or "",
                "shapeId": row.get("shape_id") or "",
            }
            for row in reader
            if row["service_id"] in active_services
        }


def _read_departures(
    archive: zipfile.ZipFile,
    stops: dict[str, Stop],
    stop_ids: set[str],
    routes: dict[str, dict[str, str]],
    trips: dict[str, dict[str, str]],
    now: datetime,
    horizon_minutes: int,
    limit: int,
) -> list[dict[str, Any]]:
    current_seconds = now.hour * 3600 + now.minute * 60 + now.second
    horizon_seconds = current_seconds + horizon_minutes * 60
    rows: list[dict[str, Any]] = []

    with archive.open("stop_times.txt") as file:
        reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
        for row in reader:
            stop_id = row["stop_id"]
            trip = trips.get(row["trip_id"])
            if stop_id not in stop_ids or trip is None:
                continue

            time_value = row.get("departure_time") or row.get("arrival_time") or ""
            if not time_value:
                continue

            departure_seconds = _gtfs_time_to_seconds(time_value)
            if departure_seconds < current_seconds or departure_seconds > horizon_seconds:
                continue

            route = routes.get(trip["routeId"], {})
            stop = stops[stop_id]
            minutes = round((departure_seconds - current_seconds) / 60)
            rows.append(
                {
                    "routeId": route.get("routeId"),
                    "route": route.get("shortName"),
                    "routeName": route.get("longName"),
                    "routeType": _route_type_label(route.get("type")),
                    "headsign": trip["headsign"],
                    "stopId": stop_id,
                    "stopName": stop.name,
                    "stopDistanceMeters": stop.distance_meters,
                    "departureTime": _format_departure_time(now, departure_seconds),
                    "minutesUntil": minutes,
                }
            )

    rows.sort(
        key=lambda row: (
            row["minutesUntil"],
            row["stopDistanceMeters"],
            row["route"] or "",
        )
    )
    return rows[:limit]


def _gtfs_time_to_seconds(value: str) -> int:
    hours, minutes, seconds = [int(part) for part in value.split(":")]
    return hours * 3600 + minutes * 60 + seconds


def _format_departure_time(now: datetime, seconds: int) -> str:
    day_offset, seconds_today = divmod(seconds, 24 * 3600)
    return (
        now.replace(hour=0, minute=0, second=0, microsecond=0)
        + timedelta(days=day_offset, seconds=seconds_today)
    ).isoformat()


def _route_type_label(route_type: str | None) -> str:
    return {
        "0": "tram",
        "1": "metro",
        "2": "rail",
        "3": "bus",
        "4": "ferry",
    }.get(route_type or "", "transit")
