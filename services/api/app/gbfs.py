from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.geo import travel_estimates_from_origin

GBFS_MANIFEST_URL = os.getenv(
    "GBFS_MANIFEST_URL",
    "https://gbfs.velobixi.com/gbfs/2-2/gbfs.json",
)


class GbfsError(RuntimeError):
    """Raised when the BIXI GBFS feed cannot be loaded."""


async def fetch_live_stations() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=12) as client:
        station_info_url, station_status_url = await _resolve_feed_urls(client)
        info_response, status_response = await _fetch_json(
            client,
            station_info_url,
            station_status_url,
        )

    info_stations = info_response.get("data", {}).get("stations", [])
    status_stations = status_response.get("data", {}).get("stations", [])
    status_by_id = {station["station_id"]: station for station in status_stations}

    stations = [
        _normalize_station(info, status_by_id.get(info["station_id"], {}))
        for info in info_stations
    ]

    stations.sort(key=lambda station: station["riskScore"], reverse=True)

    return {
        "source": "bixi-gbfs",
        "updatedAt": datetime.now(UTC).isoformat(),
        "count": len(stations),
        "stations": stations,
    }


async def _resolve_feed_urls(client: httpx.AsyncClient) -> tuple[str, str]:
    manifest = (await _fetch_json(client, GBFS_MANIFEST_URL))[0]
    feeds = manifest.get("data", {}).get("en", {}).get("feeds")

    if not feeds:
        feeds = manifest.get("data", {}).get("feeds", [])

    urls = {feed.get("name"): feed.get("url") for feed in feeds}
    station_info_url = urls.get("station_information")
    station_status_url = urls.get("station_status")

    if not station_info_url or not station_status_url:
        raise GbfsError("GBFS manifest is missing station feeds")

    return station_info_url, station_status_url


async def _fetch_json(
    client: httpx.AsyncClient,
    *urls: str,
) -> tuple[dict[str, Any], ...]:
    responses = await asyncio_gather_json(client, urls)
    return tuple(responses)


async def asyncio_gather_json(
    client: httpx.AsyncClient,
    urls: tuple[str, ...],
) -> list[dict[str, Any]]:
    import asyncio

    async def get_json(url: str) -> dict[str, Any]:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

    return await asyncio.gather(*(get_json(url) for url in urls))


def _normalize_station(info: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    latitude = info.get("lat")
    longitude = info.get("lon")
    capacity = int(info.get("capacity") or 0)
    bikes = int(status.get("num_bikes_available") or 0)
    ebikes = int(status.get("num_ebikes_available") or 0)
    docks = int(status.get("num_docks_available") or 0)
    installed = bool(status.get("is_installed", 0))
    renting = bool(status.get("is_renting", 0))
    returning = bool(status.get("is_returning", 0))
    risk_score = _risk_score(capacity, bikes, docks, installed, renting, returning)

    return {
        "stationId": info["station_id"],
        "name": info.get("name", "Unknown station"),
        "shortName": info.get("short_name"),
        "latitude": latitude,
        "longitude": longitude,
        "capacity": capacity,
        "bikesAvailable": bikes,
        "ebikesAvailable": ebikes,
        "docksAvailable": docks,
        "isInstalled": installed,
        "isRenting": renting,
        "isReturning": returning,
        "lastReported": _format_reported_at(status.get("last_reported")),
        "riskScore": risk_score,
        "status": _status_label(risk_score, bikes, docks, installed, renting, returning),
        **travel_estimates_from_origin(latitude, longitude),
    }


def _risk_score(
    capacity: int,
    bikes: int,
    docks: int,
    installed: bool,
    renting: bool,
    returning: bool,
) -> int:
    if not installed or not renting or not returning:
        return 100

    if bikes == 0 or docks == 0:
        return 100

    capacity_floor = max(capacity, bikes + docks, 1)
    bike_pressure = 1 - min(bikes / max(capacity_floor * 0.25, 1), 1)
    dock_pressure = 1 - min(docks / max(capacity_floor * 0.25, 1), 1)
    return round(max(bike_pressure, dock_pressure) * 100)


def _status_label(
    risk_score: int,
    bikes: int,
    docks: int,
    installed: bool,
    renting: bool,
    returning: bool,
) -> str:
    if not installed or not renting or not returning:
        return "offline"

    if bikes == 0 or docks == 0:
        return "critical"

    if risk_score >= 50:
        return "warning"

    return "healthy"


def _format_reported_at(last_reported: int | None) -> str | None:
    if not last_reported:
        return None

    return datetime.fromtimestamp(last_reported, UTC).isoformat()
