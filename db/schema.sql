CREATE TABLE IF NOT EXISTS stations (
    station_id text PRIMARY KEY,
    name text NOT NULL,
    short_name text,
    latitude double precision NOT NULL,
    longitude double precision NOT NULL,
    capacity integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS station_snapshots (
    id bigserial PRIMARY KEY,
    station_id text NOT NULL REFERENCES stations(station_id) ON DELETE CASCADE,
    observed_at timestamptz NOT NULL,
    bikes_available integer NOT NULL,
    ebikes_available integer NOT NULL,
    docks_available integer NOT NULL,
    is_installed boolean NOT NULL,
    is_renting boolean NOT NULL,
    is_returning boolean NOT NULL,
    risk_score integer NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS station_snapshots_station_observed_idx
    ON station_snapshots (station_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS station_snapshots_observed_idx
    ON station_snapshots (observed_at DESC);
