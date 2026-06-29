# Moogle Maps

Smart Montreal mobility telemetry for BIXI station availability, risk scoring, and route-aware recommendations.

## Current Slice

- React + TypeScript dashboard with a live station map, search, filters, station detail metrics, and availability trend preview.
- FastAPI service that reads the official BIXI GBFS manifest and normalizes station information/status feeds.
- CSV cold-history capture that stores live station snapshots every minute while the API is running.
- Python collector that stores station snapshots into CSV and PostgreSQL for historical analytics.
- PostgreSQL schema and Docker Compose service for local data collection.
- Fixed origin at Concordia University's Henry F. Hall Building for walking/biking estimates.
- Open-Meteo current weather and official STM GTFS static schedule endpoints.
- Debounced place search backed by Nominatim and multimodal route options backed by OSRM geometry, BIXI station availability, STM GTFS, weather, and local Dijkstra scoring.

## Run Locally

```bash
npm install
npm --prefix apps/web install
python3 -m venv .venv
source .venv/bin/activate
pip install -r services/api/requirements.txt
cp .env.example .env
```

Start the API:

```bash
source .venv/bin/activate
uvicorn app.main:app --app-dir services/api --reload --port 8000
```

The API auto-appends live BIXI snapshots to `data/station_snapshots.csv` every 60 seconds by default. Set `CSV_AUTO_COLLECT=false` to disable that loop.

Start the web app:

```bash
npm run dev:web
```

Optional database and collector:

```bash
docker compose -f infra/docker-compose.yml up -d postgres
source .venv/bin/activate
python services/collector/collector.py --once
```

Useful data endpoints:

```bash
curl http://127.0.0.1:8000/api/context/origin
curl "http://127.0.0.1:8000/api/places/search?q=McGill%20University"
curl -X POST http://127.0.0.1:8000/api/routes/options \
  -H "Content-Type: application/json" \
  -d '{"name":"McGill University","latitude":45.5068861,"longitude":-73.5787118}'
curl http://127.0.0.1:8000/api/weather/current
curl http://127.0.0.1:8000/api/transit/departures
curl http://127.0.0.1:8000/api/history/summary
curl http://127.0.0.1:8000/api/stations/1/history
```

## Data Source

Moogle Maps uses BIXI's public GBFS feed:

- Manifest: `https://gbfs.velobixi.com/gbfs/2-2/gbfs.json`
- Station information: resolved from the manifest.
- Station status: resolved from the manifest.

Moogle Maps also uses:

- Weather: Open-Meteo forecast API.
- Transit: STM static GTFS schedule zip.
- Place search: Nominatim search API, bounded to Montreal.
- Route geometry: OSRM route API.

## Next Build Steps

1. Add API endpoints for historical station snapshots from PostgreSQL.
2. Replace the trend preview with real historical charts.
3. Add origin/destination route planning with safest start and end stations.
4. Add a simple prediction endpoint for 20-30 minute bike/dock availability.
