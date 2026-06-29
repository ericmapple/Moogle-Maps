from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.csv_history import (
    append_station_snapshots_async,
    csv_auto_collect_enabled,
    csv_collect_interval_seconds,
    history_summary,
    read_station_history,
)
from app.geo import ORIGIN
from app.gbfs import GbfsError, fetch_live_stations
from app.places import PlacesError, search_places
from app.routing import Point, build_route_options
from app.transit import fetch_departures
from app.weather import WeatherError, fetch_weather


@asynccontextmanager
async def lifespan(_: FastAPI):
    task: asyncio.Task | None = None
    if csv_auto_collect_enabled():
        task = asyncio.create_task(_csv_collection_loop())

    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Moogle Maps API",
    version="0.1.0",
    description="Live BIXI telemetry API for the Moogle Maps mobility dashboard.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RouteRequest(BaseModel):
    name: str = Field(min_length=1)
    latitude: float
    longitude: float


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/context/origin")
async def origin() -> dict:
    return {"origin": ORIGIN}


@app.get("/api/places/search")
async def places_search(
    q: str = Query(min_length=1),
    limit: int = Query(default=6, ge=1, le=10),
) -> dict:
    try:
        return await search_places(q, limit=limit)
    except PlacesError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/routes/options")
async def route_options(request: RouteRequest) -> dict:
    return await build_route_options(
        Point(
            name=request.name,
            latitude=request.latitude,
            longitude=request.longitude,
        )
    )


@app.get("/api/stations/live")
async def live_stations() -> dict:
    try:
        return await fetch_live_stations()
    except GbfsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/stations/top-risk")
async def top_risk(
    limit: int = Query(default=12, ge=1, le=50),
) -> dict:
    payload = await live_stations()
    return {
        "source": payload["source"],
        "updatedAt": payload["updatedAt"],
        "stations": payload["stations"][:limit],
    }


@app.post("/api/history/snapshot")
async def save_history_snapshot() -> dict:
    payload = await live_stations()
    rows = await append_station_snapshots_async(payload)
    return {"savedRows": rows, "summary": history_summary()}


@app.get("/api/history/summary")
async def get_history_summary() -> dict:
    return history_summary()


@app.get("/api/stations/{station_id}/history")
async def station_history(
    station_id: str,
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=288, ge=1, le=2_000),
) -> dict:
    return {
        "stationId": station_id,
        "source": "csv-history",
        "history": read_station_history(station_id, hours=hours, limit=limit),
    }


@app.get("/api/weather/current")
async def current_weather() -> dict:
    try:
        return await fetch_weather()
    except WeatherError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/transit/departures")
async def transit_departures(
    limit: int = Query(default=10, ge=1, le=50),
    radius_meters: int = Query(default=650, ge=100, le=2_000),
    horizon_minutes: int = Query(default=90, ge=5, le=240),
) -> dict:
    return await fetch_departures(
        limit=limit,
        radius_meters=radius_meters,
        horizon_minutes=horizon_minutes,
    )


async def _csv_collection_loop() -> None:
    interval = csv_collect_interval_seconds()
    while True:
        try:
            payload = await fetch_live_stations()
            await append_station_snapshots_async(payload)
        except Exception as exc:
            print(f"CSV collection failed: {exc}")
        await asyncio.sleep(interval)
