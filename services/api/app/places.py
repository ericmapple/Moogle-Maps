from __future__ import annotations

import os
from typing import Any

import httpx

MONTREAL_VIEWBOX = "-73.75,45.7,-73.45,45.4"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"


class PlacesError(RuntimeError):
    """Raised when place search cannot be completed."""


async def search_places(query: str, *, limit: int = 6) -> dict[str, Any]:
    normalized = query.strip()
    if len(normalized) < 3:
        return {"source": "nominatim", "query": query, "places": []}

    headers = {
        "User-Agent": os.getenv(
            "NOMINATIM_USER_AGENT",
            "MoogleMaps/0.1 student-project",
        )
    }
    params = {
        "q": normalized,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": min(limit, 10),
        "viewbox": MONTREAL_VIEWBOX,
        "bounded": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=12, headers=headers) as client:
            response = await client.get(NOMINATIM_SEARCH_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise PlacesError("Place search is temporarily unavailable") from exc

    return {
        "source": "nominatim",
        "query": normalized,
        "places": [_normalize_place(place) for place in payload],
    }


def _normalize_place(place: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(place.get("place_id")),
        "name": place.get("name") or place.get("display_name", "").split(",")[0],
        "label": place.get("display_name"),
        "latitude": float(place["lat"]),
        "longitude": float(place["lon"]),
        "category": place.get("category"),
        "type": place.get("type"),
        "importance": place.get("importance"),
    }
