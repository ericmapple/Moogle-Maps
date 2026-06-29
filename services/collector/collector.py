from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from dotenv import load_dotenv

API_PATH = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(API_PATH))

from app.csv_history import append_station_snapshots  # noqa: E402
from app.gbfs import fetch_live_stations  # noqa: E402

load_dotenv()


def database_url() -> str | None:
    return os.getenv("DATABASE_URL")


async def collect_once() -> int:
    payload = await fetch_live_stations()
    observed_at = datetime.now(UTC)
    append_station_snapshots(payload)
    connection_url = database_url()
    if not connection_url:
        return payload["count"]

    with psycopg.connect(connection_url) as conn:
        with conn.cursor() as cur:
            for station in payload["stations"]:
                cur.execute(
                    """
                    INSERT INTO stations (
                        station_id,
                        name,
                        short_name,
                        latitude,
                        longitude,
                        capacity,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (station_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        short_name = EXCLUDED.short_name,
                        latitude = EXCLUDED.latitude,
                        longitude = EXCLUDED.longitude,
                        capacity = EXCLUDED.capacity,
                        updated_at = now()
                    """,
                    (
                        station["stationId"],
                        station["name"],
                        station.get("shortName"),
                        station["latitude"],
                        station["longitude"],
                        station["capacity"],
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO station_snapshots (
                        station_id,
                        observed_at,
                        bikes_available,
                        ebikes_available,
                        docks_available,
                        is_installed,
                        is_renting,
                        is_returning,
                        risk_score,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        station["stationId"],
                        observed_at,
                        station["bikesAvailable"],
                        station["ebikesAvailable"],
                        station["docksAvailable"],
                        station["isInstalled"],
                        station["isRenting"],
                        station["isReturning"],
                        station["riskScore"],
                        station["status"],
                    ),
                )

    return payload["count"]


async def collect_loop(interval: int) -> None:
    while True:
        count = await collect_once()
        print(f"{datetime.now(UTC).isoformat()} stored {count} station snapshots")
        await asyncio.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect BIXI station snapshots.")
    parser.add_argument("--once", action="store_true", help="Collect one snapshot and exit.")
    parser.add_argument("--interval", type=int, default=300, help="Loop interval in seconds.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.once:
        count = asyncio.run(collect_once())
        print(f"stored {count} station snapshots")
        return

    asyncio.run(collect_loop(args.interval))


if __name__ == "__main__":
    main()
