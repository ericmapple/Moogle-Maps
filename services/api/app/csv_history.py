from __future__ import annotations

import asyncio
import csv
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parents[3] / "data" / "station_snapshots.csv"

CSV_FIELDS = [
    "observed_at",
    "source",
    "station_id",
    "name",
    "short_name",
    "latitude",
    "longitude",
    "capacity",
    "bikes_available",
    "ebikes_available",
    "docks_available",
    "is_installed",
    "is_renting",
    "is_returning",
    "risk_score",
    "status",
    "distance_from_origin_meters",
    "walk_minutes",
    "bike_minutes",
]


def history_path() -> Path:
    return Path(os.getenv("CSV_HISTORY_PATH", str(DEFAULT_HISTORY_PATH)))


def csv_auto_collect_enabled() -> bool:
    return os.getenv("CSV_AUTO_COLLECT", "true").lower() in {"1", "true", "yes", "on"}


def csv_collect_interval_seconds() -> int:
    return int(os.getenv("CSV_COLLECT_INTERVAL_SECONDS", "60"))


def append_station_snapshots(payload: dict[str, Any]) -> int:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    should_write_header = not path.exists() or path.stat().st_size == 0
    observed_at = payload.get("updatedAt") or datetime.now(UTC).isoformat()

    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        if should_write_header:
            writer.writeheader()

        for station in payload["stations"]:
            writer.writerow(
                {
                    "observed_at": observed_at,
                    "source": payload.get("source", "unknown"),
                    "station_id": station["stationId"],
                    "name": station["name"],
                    "short_name": station.get("shortName"),
                    "latitude": station["latitude"],
                    "longitude": station["longitude"],
                    "capacity": station["capacity"],
                    "bikes_available": station["bikesAvailable"],
                    "ebikes_available": station["ebikesAvailable"],
                    "docks_available": station["docksAvailable"],
                    "is_installed": station["isInstalled"],
                    "is_renting": station["isRenting"],
                    "is_returning": station["isReturning"],
                    "risk_score": station["riskScore"],
                    "status": station["status"],
                    "distance_from_origin_meters": station["distanceFromOriginMeters"],
                    "walk_minutes": station["walkMinutes"],
                    "bike_minutes": station["bikeMinutes"],
                }
            )

    return len(payload["stations"])


async def append_station_snapshots_async(payload: dict[str, Any]) -> int:
    return await asyncio.to_thread(append_station_snapshots, payload)


def read_station_history(
    station_id: str,
    *,
    hours: int = 24,
    limit: int = 288,
) -> list[dict[str, Any]]:
    path = history_path()
    if not path.exists():
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    rows: list[dict[str, Any]] = []

    with path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("station_id") != station_id:
                continue

            observed_at = _parse_iso(row["observed_at"])
            if observed_at < cutoff:
                continue

            rows.append(
                {
                    "observedAt": observed_at.isoformat(),
                    "stationId": row["station_id"],
                    "bikesAvailable": int(row["bikes_available"]),
                    "ebikesAvailable": int(row["ebikes_available"]),
                    "docksAvailable": int(row["docks_available"]),
                    "riskScore": int(row["risk_score"]),
                    "status": row["status"],
                }
            )

    return rows[-limit:]


def history_summary() -> dict[str, Any]:
    path = history_path()
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "rows": 0,
            "updatedAt": None,
        }

    rows = 0
    latest: str | None = None
    with path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            rows += 1
            latest = row.get("observed_at") or latest

    return {
        "path": str(path),
        "exists": True,
        "rows": rows,
        "updatedAt": latest,
    }


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)
