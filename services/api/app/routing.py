from __future__ import annotations

import asyncio
import csv
import heapq
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx

from app.gbfs import fetch_live_stations
from app.geo import (
    BIKING_SPEED_METERS_PER_MINUTE,
    ORIGIN,
    WALKING_SPEED_METERS_PER_MINUTE,
    distance_meters,
)
from app.transit import (
    MONTREAL_TZ,
    _active_service_ids,
    _format_departure_time,
    _gtfs_time_to_seconds,
    _nearby_stops,
    _read_active_trips,
    _read_routes,
    _route_type_label,
    ensure_gtfs_zip,
)
from app.weather import WeatherError, fetch_weather

OSRM_URL = os.getenv("OSRM_URL", "https://router.project-osrm.org")
RouteMode = Literal["walk", "bike", "bixi", "transit"]


@dataclass(frozen=True)
class Point:
    latitude: float
    longitude: float
    name: str


@dataclass(frozen=True)
class GraphEdge:
    to_node: str
    cost: float
    duration_minutes: int
    distance_meters: int
    label: str
    geometry: list[list[float]]
    profile: str | None = None
    start: Point | None = None
    end: Point | None = None
    station_role: str | None = None
    station: dict[str, Any] | None = None


async def build_route_options(destination: Point) -> dict[str, Any]:
    weather_task = asyncio.create_task(_weather_context())
    stations_task = asyncio.create_task(fetch_live_stations())

    walk_task = asyncio.create_task(_osrm_route("foot", ORIGIN_POINT, destination))
    bike_task = asyncio.create_task(_osrm_route("bike", ORIGIN_POINT, destination))

    stations_payload = await stations_task
    weather = await weather_task

    walk, bike, bixi, transit_options = await asyncio.gather(
        _walk_option(walk_task, weather),
        _bike_option(bike_task, weather),
        _bixi_option(destination, stations_payload["stations"], weather),
        _transit_options(destination, weather),
    )

    options = [
        option
        for option in [walk, bike, bixi, *transit_options]
        if option is not None
    ]
    options.sort(key=lambda option: (-option["rating"], option["durationMinutes"]))

    return {
        "origin": ORIGIN,
        "destination": {
            "name": destination.name,
            "latitude": destination.latitude,
            "longitude": destination.longitude,
        },
        "algorithm": "Dijkstra over candidate multimodal legs",
        "weather": weather,
        "options": options,
    }


ORIGIN_POINT = Point(
    latitude=ORIGIN["latitude"],
    longitude=ORIGIN["longitude"],
    name=ORIGIN["name"],
)


async def _walk_option(route_task: asyncio.Task, weather: dict[str, Any] | None) -> dict[str, Any]:
    route = await route_task
    score = route.duration_minutes + _weather_penalty(weather, "walk")
    return _with_rating({
        "id": "walk",
        "mode": "walk",
        "title": "Walk",
        "durationMinutes": route.duration_minutes,
        "distanceMeters": route.distance_meters,
        "score": round(score, 1),
        "scoreLabel": _score_label(score),
        "summary": "Direct walking route from Concordia.",
        "exploredNodes": route.explored_nodes,
        "searchSteps": route.explored_nodes,
        "legs": [
            {
                "mode": "walk",
                "label": "Walk to destination",
                "durationMinutes": route.duration_minutes,
                "distanceMeters": route.distance_meters,
            }
        ],
        "geometry": route.geometry,
    }, score, route.explored_nodes)


async def _bike_option(route_task: asyncio.Task, weather: dict[str, Any] | None) -> dict[str, Any]:
    route = await route_task
    score = route.duration_minutes + _weather_penalty(weather, "bike")
    return _with_rating({
        "id": "bike",
        "mode": "bike",
        "title": "Bike",
        "durationMinutes": route.duration_minutes,
        "distanceMeters": route.distance_meters,
        "score": round(score, 1),
        "scoreLabel": _score_label(score),
        "summary": "Direct cycling route using OSM routing.",
        "exploredNodes": route.explored_nodes,
        "searchSteps": route.explored_nodes,
        "legs": [
            {
                "mode": "bike",
                "label": "Bike to destination",
                "durationMinutes": route.duration_minutes,
                "distanceMeters": route.distance_meters,
            }
        ],
        "geometry": route.geometry,
    }, score, route.explored_nodes)


async def _bixi_option(
    destination: Point,
    stations: list[dict[str, Any]],
    weather: dict[str, Any] | None,
) -> dict[str, Any] | None:
    starts = sorted(
        (
            station
            for station in stations
            if station["bikesAvailable"] > 0 and station["isRenting"]
        ),
        key=lambda station: _bixi_station_rank(
            station,
            "pickup",
            station.get("distanceFromOriginMeters")
            or distance_meters(
                ORIGIN["latitude"],
                ORIGIN["longitude"],
                station["latitude"],
                station["longitude"],
            ),
        ),
    )[:9]
    ends = sorted(
        (
            {
                **station,
                "distanceToDestinationMeters": distance_meters(
                    station["latitude"],
                    station["longitude"],
                    destination.latitude,
                    destination.longitude,
                ),
            }
            for station in stations
            if station["docksAvailable"] > 0 and station["isReturning"]
        ),
        key=lambda station: _bixi_station_rank(
            station,
            "dropoff",
            station["distanceToDestinationMeters"],
        ),
    )[:9]

    if not starts or not ends:
        return None

    graph: dict[str, list[GraphEdge]] = {"origin": []}
    for index, station in enumerate(starts):
        node = f"start:{index}"
        graph[node] = []
        station_point = _station_point(station)
        route = _straight_line_route("foot", ORIGIN_POINT, station_point)
        graph["origin"].append(
            GraphEdge(
                to_node=node,
                cost=route.duration_minutes + _bixi_availability_penalty(station, "pickup"),
                duration_minutes=route.duration_minutes,
                distance_meters=route.distance_meters,
                label=f"Walk to {station['name']}",
                geometry=route.geometry,
                profile="foot",
                start=ORIGIN_POINT,
                end=station_point,
                station_role="pickup",
                station=station,
            )
        )

        for end_index, end_station in enumerate(ends):
            end_node = f"end:{end_index}"
            graph.setdefault(end_node, [])
            pickup_point = _station_point(station)
            dropoff_point = _station_point(end_station)
            bike_route = _straight_line_route("bike", pickup_point, dropoff_point)
            graph[node].append(
                GraphEdge(
                    to_node=end_node,
                    cost=bike_route.duration_minutes,
                    duration_minutes=bike_route.duration_minutes,
                    distance_meters=bike_route.distance_meters,
                    label=f"BIXI from {station['name']} to {end_station['name']}",
                    geometry=bike_route.geometry,
                    profile="bike",
                    start=pickup_point,
                    end=dropoff_point,
                )
            )

    for index, station in enumerate(ends):
        end_node = f"end:{index}"
        station_point = _station_point(station)
        route = _straight_line_route("foot", station_point, destination)
        graph[end_node].append(
            GraphEdge(
                to_node="destination",
                cost=route.duration_minutes + _bixi_availability_penalty(station, "dropoff"),
                duration_minutes=route.duration_minutes,
                distance_meters=route.distance_meters,
                label=f"Walk from {station['name']}",
                geometry=route.geometry,
                profile="foot",
                start=station_point,
                end=destination,
                station_role="dropoff",
                station=station,
            )
        )

    result = _dijkstra(graph, "origin", "destination")
    if result is None:
        return None

    resolved_edges = await _resolve_route_edges(result["edges"])
    duration = sum(edge["durationMinutes"] for edge in resolved_edges)
    distance = sum(edge["distanceMeters"] for edge in resolved_edges)
    availability_penalty = result["cost"] - sum(edge.duration_minutes for edge in result["edges"])
    score = duration + availability_penalty + _weather_penalty(weather, "bike")
    pickup = next(
        (edge.station for edge in result["edges"] if edge.station_role == "pickup"),
        None,
    )
    dropoff = next(
        (edge.station for edge in result["edges"] if edge.station_role == "dropoff"),
        None,
    )

    return _with_rating({
        "id": "bixi",
        "mode": "bixi",
        "title": "BIXI",
        "durationMinutes": duration,
        "distanceMeters": distance,
        "score": round(score, 1),
        "scoreLabel": _score_label(score),
        "summary": "Best bike-share route found by scoring nearby pickup and dock stations.",
        "exploredNodes": result["exploredNodes"],
        "searchSteps": result["exploredNodes"],
        "bixiStations": [
            station
            for station in [
                _bixi_station_summary("pickup", pickup),
                _bixi_station_summary("dropoff", dropoff),
            ]
            if station is not None
        ],
        "legs": [
            {
                "mode": _leg_mode(edge["label"]),
                "label": edge["label"],
                "durationMinutes": edge["durationMinutes"],
                "distanceMeters": edge["distanceMeters"],
            }
            for edge in resolved_edges
        ],
        "geometry": _merge_geometry([edge["geometry"] for edge in resolved_edges]),
    }, score, result["exploredNodes"])


async def _transit_options(
    destination: Point,
    weather: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    direct_routes = await _direct_transit_routes(destination)
    weather_penalty = _weather_penalty(weather, "walk") * 0.35
    options: list[dict[str, Any]] = []

    for direct in direct_routes:
        score = direct["durationMinutes"] + direct["crowdingPenalty"] + weather_penalty
        options.append(
            _with_rating(
                {
                    "id": f"transit-{direct['routeType']}",
                    "mode": "transit",
                    "title": _transit_title(direct["routeType"]),
                    "durationMinutes": direct["durationMinutes"],
                    "distanceMeters": direct["distanceMeters"],
                    "score": round(score, 1),
                    "scoreLabel": _score_label(score),
                    "summary": (
                        f"One-seat STM {direct['routeType']} option "
                        "from the static GTFS schedule."
                    ),
                    "exploredNodes": direct["examinedTrips"],
                    "searchSteps": direct["examinedTrips"],
                    "legs": direct["legs"],
                    "geometry": direct["geometry"],
                },
                score,
                direct["examinedTrips"],
            )
        )

    return options


@dataclass(frozen=True)
class RouteResult:
    duration_minutes: int
    distance_meters: int
    geometry: list[list[float]]
    explored_nodes: int


async def _cached_route(
    cache: dict[tuple[str, str, str], RouteResult],
    profile: str,
    start: Point,
    end: Point,
) -> RouteResult:
    key = (profile, _point_key(start), _point_key(end))
    if key not in cache:
        cache[key] = await _osrm_route(profile, start, end)

    return cache[key]


async def _osrm_route(profile: str, start: Point, end: Point) -> RouteResult:
    url = (
        f"{OSRM_URL}/route/v1/{profile}/"
        f"{start.longitude},{start.latitude};{end.longitude},{end.latitude}"
    )
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "Ok":
                raise RuntimeError(payload.get("message") or "No route")
            route = payload["routes"][0]
            distance = round(route["distance"])
            return RouteResult(
                duration_minutes=_duration_from_distance(profile, distance),
                distance_meters=distance,
                geometry=[[lat, lon] for lon, lat in route["geometry"]["coordinates"]],
                explored_nodes=len(route["geometry"]["coordinates"]),
            )
    except Exception:
        return _straight_line_route(profile, start, end)


def _straight_line_route(profile: str, start: Point, end: Point) -> RouteResult:
    meters = distance_meters(start.latitude, start.longitude, end.latitude, end.longitude)
    return RouteResult(
        duration_minutes=_duration_from_distance(profile, meters),
        distance_meters=meters,
        geometry=[
            [start.latitude, start.longitude],
            [end.latitude, end.longitude],
        ],
        explored_nodes=2,
    )


def _duration_from_distance(profile: str, meters: int) -> int:
    speed = (
        BIKING_SPEED_METERS_PER_MINUTE
        if profile == "bike"
        else WALKING_SPEED_METERS_PER_MINUTE
    )
    return max(1, round(meters / speed))


async def _direct_transit_routes(destination: Point) -> list[dict[str, Any]]:
    path = await ensure_gtfs_zip()
    now = datetime.now(MONTREAL_TZ)
    origin_radius = 700
    destination_radius = 850
    best_routes: list[dict[str, Any]] = []

    with zipfile.ZipFile(path) as archive:
        origin_stops = _nearby_stops(archive, origin_radius)
        destination_stops = _stops_near_point(archive, destination, destination_radius)
        if not origin_stops or not destination_stops:
            return []

        origin_ids = set(origin_stops)
        destination_ids = set(destination_stops)
        active_services = _active_service_ids(archive, now)
        routes = _read_routes(archive)
        trips = _read_active_trips(archive, active_services)
        candidates = _read_direct_transit_candidates(
            archive,
            origin_stops,
            destination_stops,
            origin_ids,
            destination_ids,
            routes,
            trips,
            now,
        )
        if candidates:
            best_routes = _best_transit_candidates_by_type(candidates)
            for candidate in best_routes:
                candidate["transitGeometry"] = _shape_segment(
                    archive,
                    candidate.get("shapeId") or "",
                    candidate["originStop"],
                    candidate["destinationStop"],
                )

    if not best_routes:
        return []

    return await asyncio.gather(
        *(_resolve_transit_candidate(candidate, destination) for candidate in best_routes)
    )


def _stops_near_point(
    archive: zipfile.ZipFile,
    point: Point,
    radius_meters: int,
) -> dict[str, Any]:
    stops: dict[str, Any] = {}
    with archive.open("stops.txt") as file:
        reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
        for row in reader:
            latitude = float(row["stop_lat"])
            longitude = float(row["stop_lon"])
            meters = distance_meters(point.latitude, point.longitude, latitude, longitude)
            if meters <= radius_meters:
                stops[row["stop_id"]] = {
                    "stopId": row["stop_id"],
                    "name": row["stop_name"],
                    "latitude": latitude,
                    "longitude": longitude,
                    "distanceMeters": meters,
                }

    return stops


def _read_direct_transit_candidates(
    archive: zipfile.ZipFile,
    origin_stops: dict[str, Any],
    destination_stops: dict[str, Any],
    origin_ids: set[str],
    destination_ids: set[str],
    routes: dict[str, dict[str, str]],
    trips: dict[str, dict[str, str]],
    now: datetime,
) -> list[dict[str, Any]]:
    current_seconds = now.hour * 3600 + now.minute * 60 + now.second
    horizon_seconds = current_seconds + 120 * 60
    by_trip: dict[str, dict[str, list[dict[str, Any]]]] = {}
    examined_rows = 0

    with archive.open("stop_times.txt") as file:
        reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
        for row in reader:
            trip = trips.get(row["trip_id"])
            if trip is None:
                continue

            stop_id = row["stop_id"]
            if stop_id not in origin_ids and stop_id not in destination_ids:
                continue

            time_value = row.get("departure_time") or row.get("arrival_time") or ""
            if not time_value:
                continue

            seconds = _gtfs_time_to_seconds(time_value)
            if seconds > horizon_seconds:
                continue

            examined_rows += 1
            bucket = by_trip.setdefault(row["trip_id"], {"origin": [], "destination": []})
            entry = {
                "stopId": stop_id,
                "sequence": int(row["stop_sequence"]),
                "seconds": seconds,
            }
            if stop_id in origin_ids:
                bucket["origin"].append(entry)
            if stop_id in destination_ids:
                bucket["destination"].append(entry)

    candidates: list[dict[str, Any]] = []
    for trip_id, buckets in by_trip.items():
        trip = trips[trip_id]
        route = routes.get(trip["routeId"], {})
        for origin_entry in buckets["origin"]:
            if origin_entry["seconds"] < current_seconds:
                continue

            for destination_entry in buckets["destination"]:
                if destination_entry["sequence"] <= origin_entry["sequence"]:
                    continue

                ride_minutes = round(
                    (destination_entry["seconds"] - origin_entry["seconds"]) / 60
                )
                if ride_minutes <= 0:
                    continue

                origin_stop = origin_stops[origin_entry["stopId"]]
                destination_stop = destination_stops[destination_entry["stopId"]]
                origin_stop_payload = {
                    "stopId": origin_stop.stop_id,
                    "name": origin_stop.name,
                    "latitude": origin_stop.latitude,
                    "longitude": origin_stop.longitude,
                    "distanceMeters": origin_stop.distance_meters,
                }
                route_type = _route_type_label(route.get("type"))
                walk_to_stop = max(
                    1,
                    round(origin_stop.distance_meters / WALKING_SPEED_METERS_PER_MINUTE),
                )
                walk_from_stop = max(
                    1,
                    round(destination_stop["distanceMeters"] / WALKING_SPEED_METERS_PER_MINUTE),
                )
                wait_minutes = round((origin_entry["seconds"] - current_seconds) / 60)
                duration = walk_to_stop + wait_minutes + ride_minutes + walk_from_stop
                crowding_penalty = _transit_crowding_penalty(now, route.get("type"))
                distance = (
                    origin_stop.distance_meters
                    + destination_stop["distanceMeters"]
                    + distance_meters(
                        origin_stop.latitude,
                        origin_stop.longitude,
                        destination_stop["latitude"],
                        destination_stop["longitude"],
                    )
                )
                candidates.append(
                    {
                        "score": duration + crowding_penalty,
                        "durationMinutes": duration,
                        "distanceMeters": distance,
                        "crowdingPenalty": crowding_penalty,
                        "examinedTrips": len(by_trip),
                        "routeType": route_type,
                        "shapeId": trip.get("shapeId") or "",
                        "originStop": origin_stop_payload,
                        "destinationStop": destination_stop,
                        "waitMinutes": wait_minutes,
                        "rideMinutes": ride_minutes,
                        "legs": [
                            {
                                "mode": "walk",
                                "label": f"Walk to {origin_stop.name}",
                                "durationMinutes": walk_to_stop,
                                "distanceMeters": origin_stop.distance_meters,
                            },
                            {
                                "mode": "transit",
                                "label": (
                                    f"{route_type.title()} {route.get('shortName')} "
                                    f"toward {trip['headsign']}"
                                ),
                                "durationMinutes": wait_minutes + ride_minutes,
                                "distanceMeters": round(distance * 0.82),
                                "departureTime": _format_departure_time(
                                    now,
                                    origin_entry["seconds"],
                                ),
                                "routeType": route_type,
                            },
                            {
                                "mode": "walk",
                                "label": f"Walk from {destination_stop['name']}",
                                "durationMinutes": walk_from_stop,
                                "distanceMeters": destination_stop["distanceMeters"],
                            },
                        ],
                        "geometry": [],
                    }
                )

    candidates.sort(key=lambda candidate: candidate["score"])
    best_by_type = _best_transit_candidates_by_type(candidates)
    top_candidates = candidates[:30]
    for candidate in best_by_type:
        if candidate not in top_candidates:
            top_candidates.append(candidate)
    return top_candidates if examined_rows else []


def _best_transit_candidates_by_type(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_type: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        route_type = candidate["routeType"]
        if route_type not in {"bus", "metro"}:
            continue
        current = best_by_type.get(route_type)
        if current is None or candidate["score"] < current["score"]:
            best_by_type[route_type] = candidate

    return sorted(best_by_type.values(), key=lambda candidate: candidate["score"])


async def _resolve_route_edges(edges: list[GraphEdge]) -> list[dict[str, Any]]:
    async def resolve(edge: GraphEdge) -> dict[str, Any]:
        route = (
            await _osrm_route(edge.profile, edge.start, edge.end)
            if edge.profile and edge.start and edge.end
            else RouteResult(
                duration_minutes=edge.duration_minutes,
                distance_meters=edge.distance_meters,
                geometry=edge.geometry,
                explored_nodes=len(edge.geometry),
            )
        )
        if _is_implausible_edge_route(route, edge):
            route = RouteResult(
                duration_minutes=edge.duration_minutes,
                distance_meters=edge.distance_meters,
                geometry=edge.geometry,
                explored_nodes=len(edge.geometry),
            )
        return {
            "label": edge.label,
            "durationMinutes": route.duration_minutes,
            "distanceMeters": route.distance_meters,
            "geometry": route.geometry,
        }

    return await asyncio.gather(*(resolve(edge) for edge in edges))


def _is_implausible_edge_route(route: RouteResult, edge: GraphEdge) -> bool:
    if edge.distance_meters <= 0:
        return False

    maximum_reasonable_distance = max(edge.distance_meters * 3, edge.distance_meters + 250)
    return route.distance_meters > maximum_reasonable_distance


async def _resolve_transit_candidate(
    candidate: dict[str, Any],
    destination: Point,
) -> dict[str, Any]:
    origin_stop = candidate["originStop"]
    destination_stop = candidate["destinationStop"]
    origin_stop_point = Point(
        latitude=origin_stop["latitude"],
        longitude=origin_stop["longitude"],
        name=origin_stop["name"],
    )
    destination_stop_point = Point(
        latitude=destination_stop["latitude"],
        longitude=destination_stop["longitude"],
        name=destination_stop["name"],
    )
    walk_to_stop, walk_from_stop = await asyncio.gather(
        _osrm_route("foot", ORIGIN_POINT, origin_stop_point),
        _osrm_route("foot", destination_stop_point, destination),
    )
    transit_geometry = candidate.pop("transitGeometry", []) or [
        [origin_stop_point.latitude, origin_stop_point.longitude],
        [destination_stop_point.latitude, destination_stop_point.longitude],
    ]
    transit_distance = _geometry_distance(transit_geometry)

    candidate["legs"][0]["durationMinutes"] = walk_to_stop.duration_minutes
    candidate["legs"][0]["distanceMeters"] = walk_to_stop.distance_meters
    candidate["legs"][1]["durationMinutes"] = candidate["waitMinutes"] + candidate["rideMinutes"]
    candidate["legs"][1]["distanceMeters"] = transit_distance
    candidate["legs"][2]["durationMinutes"] = walk_from_stop.duration_minutes
    candidate["legs"][2]["distanceMeters"] = walk_from_stop.distance_meters
    candidate["durationMinutes"] = (
        walk_to_stop.duration_minutes
        + candidate["waitMinutes"]
        + candidate["rideMinutes"]
        + walk_from_stop.duration_minutes
    )
    candidate["distanceMeters"] = (
        walk_to_stop.distance_meters
        + transit_distance
        + walk_from_stop.distance_meters
    )
    candidate["geometry"] = _merge_geometry(
        [walk_to_stop.geometry, transit_geometry, walk_from_stop.geometry]
    )
    return candidate


def _shape_segment(
    archive: zipfile.ZipFile,
    shape_id: str,
    origin_stop: dict[str, Any],
    destination_stop: dict[str, Any],
) -> list[list[float]]:
    fallback = [
        [origin_stop["latitude"], origin_stop["longitude"]],
        [destination_stop["latitude"], destination_stop["longitude"]],
    ]
    if not shape_id or "shapes.txt" not in archive.namelist():
        return fallback

    points: list[tuple[int, float, float]] = []
    with archive.open("shapes.txt") as file:
        reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
        for row in reader:
            if row["shape_id"] != shape_id:
                continue
            points.append(
                (
                    int(row["shape_pt_sequence"]),
                    float(row["shape_pt_lat"]),
                    float(row["shape_pt_lon"]),
                )
            )

    if len(points) < 2:
        return fallback

    points.sort(key=lambda point: point[0])
    origin_index = _nearest_shape_index(points, origin_stop)
    destination_index = _nearest_shape_index(points, destination_stop)
    start_index = min(origin_index, destination_index)
    end_index = max(origin_index, destination_index)
    segment = points[start_index : end_index + 1]
    if origin_index > destination_index:
        segment = list(reversed(segment))

    geometry = [[origin_stop["latitude"], origin_stop["longitude"]]]
    geometry.extend([[latitude, longitude] for _, latitude, longitude in segment])
    geometry.append([destination_stop["latitude"], destination_stop["longitude"]])
    return geometry


def _nearest_shape_index(
    points: list[tuple[int, float, float]],
    stop: dict[str, Any],
) -> int:
    return min(
        range(len(points)),
        key=lambda index: distance_meters(
            stop["latitude"],
            stop["longitude"],
            points[index][1],
            points[index][2],
        ),
    )


def _geometry_distance(geometry: list[list[float]]) -> int:
    if len(geometry) < 2:
        return 0

    return round(
        sum(
            distance_meters(start[0], start[1], end[0], end[1])
            for start, end in zip(geometry, geometry[1:])
        )
    )


def _dijkstra(
    graph: dict[str, list[GraphEdge]],
    start: str,
    goal: str,
) -> dict[str, Any] | None:
    counter = 0
    queue: list[tuple[float, int, str, list[GraphEdge]]] = [(0, counter, start, [])]
    best_costs: dict[str, float] = {start: 0}
    explored = 0

    while queue:
        cost, _, node, path = heapq.heappop(queue)
        explored += 1
        if node == goal:
            return {"cost": cost, "edges": path, "exploredNodes": explored}

        if cost > best_costs.get(node, float("inf")):
            continue

        for edge in graph.get(node, []):
            candidate = cost + edge.cost
            if candidate < best_costs.get(edge.to_node, float("inf")):
                best_costs[edge.to_node] = candidate
                counter += 1
                heapq.heappush(queue, (candidate, counter, edge.to_node, [*path, edge]))

    return None


async def _weather_context() -> dict[str, Any] | None:
    try:
        return await fetch_weather()
    except WeatherError:
        return None


def _weather_penalty(weather: dict[str, Any] | None, mode: RouteMode) -> float:
    if weather is None:
        return 0

    current = weather["current"]
    precipitation = float(current.get("precipitationMm") or 0)
    wind = float(current.get("windSpeedKmh") or 0)
    condition = current.get("condition") or ""
    penalty = 0.0

    if mode in {"walk", "bike", "bixi"}:
        penalty += precipitation * (5 if mode == "walk" else 8)
        if condition in {"Rain", "Snow", "Thunderstorm"}:
            penalty += 6 if mode == "walk" else 10
        if wind >= 25 and mode in {"bike", "bixi"}:
            penalty += 5

    return penalty


def _transit_crowding_penalty(now: datetime, route_type: str | None) -> float:
    hour = now.hour
    if route_type == "1" and (7 <= hour <= 9 or 16 <= hour <= 18):
        return 7
    if 7 <= hour <= 9 or 16 <= hour <= 18:
        return 5
    return 1.5


def _score_label(score: float) -> str:
    if score <= 18:
        return "Best"
    if score <= 35:
        return "Good"
    if score <= 55:
        return "Okay"
    return "Slow"


def _with_rating(
    payload: dict[str, Any],
    score: float,
    search_steps: int,
) -> dict[str, Any]:
    rating = _rating_from_score(score)
    payload["rating"] = rating
    payload["ratingLabel"] = _rating_label(rating)
    payload["searchSteps"] = search_steps
    return payload


def _rating_from_score(score: float) -> int:
    return max(1, min(100, round(100 - score)))


def _rating_label(rating: int) -> str:
    if rating >= 85:
        return "Excellent"
    if rating >= 70:
        return "Good"
    if rating >= 50:
        return "Fair"
    return "Low"


def _transit_title(route_type: str) -> str:
    if route_type == "metro":
        return "STM Metro"
    if route_type == "bus":
        return "STM Bus"
    return f"STM {route_type.title()}"


def _bixi_station_rank(
    station: dict[str, Any],
    role: str,
    distance: int | float,
) -> float:
    return float(distance) + _bixi_availability_penalty(station, role) * 80


def _bixi_availability_penalty(station: dict[str, Any], role: str) -> float:
    available = (
        int(station["bikesAvailable"])
        if role == "pickup"
        else int(station["docksAvailable"])
    )
    service_blocked = (
        not station["isRenting"]
        if role == "pickup"
        else not station["isReturning"]
    )
    if service_blocked or available <= 0:
        return 100

    scarcity_penalty = max(0, 4 - available) * 1.4
    risk_penalty = station["riskScore"] / 12
    confidence_bonus = min(available, 8) * 0.2
    return max(0, risk_penalty + scarcity_penalty - confidence_bonus)


def _bixi_station_summary(
    role: str,
    station: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if station is None:
        return None

    return {
        "role": role,
        "stationId": station["stationId"],
        "name": station["name"],
        "latitude": station["latitude"],
        "longitude": station["longitude"],
        "status": station["status"],
        "riskScore": station["riskScore"],
        "bikesAvailable": station["bikesAvailable"],
        "docksAvailable": station["docksAvailable"],
    }


def _station_point(station: dict[str, Any]) -> Point:
    return Point(
        latitude=station["latitude"],
        longitude=station["longitude"],
        name=station["name"],
    )


def _point_key(point: Point) -> str:
    return f"{point.latitude:.6f},{point.longitude:.6f}"


def _leg_mode(label: str) -> str:
    return "bike" if label.startswith("BIXI") else "walk"


def _merge_geometry(geometries: list[list[list[float]]]) -> list[list[float]]:
    merged: list[list[float]] = []
    for geometry in geometries:
        for coordinate in geometry:
            if merged and merged[-1] == coordinate:
                continue
            merged.append(coordinate)
    return merged
