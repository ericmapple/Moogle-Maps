import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  BarChart3,
  Bike,
  BusFront,
  CircleGauge,
  Database,
  Footprints,
  Map as MapIcon,
  MapPin,
  RefreshCw,
  Search,
  Timer,
  TrainFront,
  X,
  Zap,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import './App.css'

type StationStatus = 'healthy' | 'warning' | 'critical' | 'offline'

type Station = {
  stationId: string
  name: string
  shortName?: string
  latitude: number
  longitude: number
  capacity: number
  bikesAvailable: number
  ebikesAvailable: number
  docksAvailable: number
  isInstalled: boolean
  isRenting: boolean
  isReturning: boolean
  lastReported: string | null
  riskScore: number
  status: StationStatus
  distanceFromOriginMeters?: number
  walkMinutes?: number
  bikeMinutes?: number
}

type StationResponse = {
  source: string
  updatedAt: string
  count: number
  stations: Station[]
}

type WeatherPayload = {
  source: string
  current: {
    time: string
    temperatureC: number
    apparentTemperatureC: number
    precipitationMm: number
    condition: string
    windSpeedKmh: number
    windGustsKmh: number
  }
  hourly: Array<{
    time: string
    temperatureC: number
    precipitationProbability: number
    condition: string
    windSpeedKmh: number
  }>
}

type TransitPayload = {
  source: string
  realtime: boolean
  departures: Array<{
    route: string
    routeType: string
    headsign: string
    stopName: string
    stopDistanceMeters: number
    minutesUntil: number
  }>
}

type HistorySummary = {
  exists: boolean
  rows: number
  updatedAt: string | null
  path: string
}

type PlaceSuggestion = {
  id: string
  name: string
  label: string
  latitude: number
  longitude: number
  category?: string
  type?: string
}

type RouteLeg = {
  mode: string
  label: string
  durationMinutes: number
  distanceMeters: number
  departureTime?: string
  routeType?: string
}

type BixiRouteStation = {
  role: 'pickup' | 'dropoff'
  stationId: string
  name: string
  latitude: number
  longitude: number
  status: StationStatus
  riskScore: number
  bikesAvailable: number
  docksAvailable: number
}

type RouteOption = {
  id: string
  mode: 'walk' | 'bike' | 'bixi' | 'transit'
  title: string
  durationMinutes: number
  distanceMeters: number
  score: number
  scoreLabel: string
  rating: number
  ratingLabel: string
  summary: string
  exploredNodes: number
  searchSteps: number
  bixiStations?: BixiRouteStation[]
  legs: RouteLeg[]
  geometry: Array<[number, number]>
}

type RouteResponse = {
  algorithm: string
  destination: {
    name: string
    latitude: number
    longitude: number
  }
  options: RouteOption[]
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

const demoStations: Station[] = [
  {
    stationId: '1',
    name: 'Drummond / de Maisonneuve',
    shortName: '6001',
    latitude: 45.49965,
    longitude: -73.57633,
    capacity: 23,
    bikesAvailable: 1,
    ebikesAvailable: 0,
    docksAvailable: 21,
    isInstalled: true,
    isRenting: true,
    isReturning: true,
    lastReported: null,
    riskScore: 86,
    status: 'warning',
  },
  {
    stationId: '12',
    name: 'Peel / Notre-Dame',
    shortName: '6012',
    latitude: 45.49428,
    longitude: -73.56383,
    capacity: 31,
    bikesAvailable: 18,
    ebikesAvailable: 3,
    docksAvailable: 12,
    isInstalled: true,
    isRenting: true,
    isReturning: true,
    lastReported: null,
    riskScore: 18,
    status: 'healthy',
  },
]

const statusTone: Record<StationStatus, string> = {
  healthy: '#16866f',
  warning: '#d69020',
  critical: '#d94b3d',
  offline: '#6f7785',
}

const routeTone: Record<RouteOption['mode'], string> = {
  walk: '#315fd9',
  bike: '#16866f',
  bixi: '#12a594',
  transit: '#101719',
}

const formatUpdatedAt = (value: string) =>
  new Intl.DateTimeFormat('en-CA', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))

const formatDistance = (meters: number) => {
  if (meters >= 1000) {
    return `${(meters / 1000).toFixed(1)} km`
  }

  return `${meters} m`
}

const buildTrend = (station: Station) => {
  const seed = Number.parseInt(station.stationId, 10) || station.capacity
  return Array.from({ length: 8 }, (_, index) => {
    const drift = Math.sin((seed + index) * 0.75) * 2.4
    const bikes = Math.max(
      0,
      Math.min(station.capacity, Math.round(station.bikesAvailable + drift + index - 3)),
    )

    return {
      time: `${index * 5}m`,
      bikes,
      docks: Math.max(0, station.capacity - bikes),
    }
  })
}

const useDebouncedValue = <T,>(value: T, delayMs: number) => {
  const [debouncedValue, setDebouncedValue] = useState(value)

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedValue(value), delayMs)
    return () => window.clearTimeout(timeout)
  }, [delayMs, value])

  return debouncedValue
}

function RouteBounds({
  route,
  destination,
}: {
  route: RouteOption | null
  destination: PlaceSuggestion | null
}) {
  const map = useMap()

  useEffect(() => {
    if (route?.geometry.length) {
      map.fitBounds(route.geometry, { padding: [70, 70], maxZoom: 15 })
      return
    }

    if (destination) {
      map.setView([destination.latitude, destination.longitude], 14)
    }
  }, [destination, map, route])

  return null
}

const routeIcon = (option: RouteOption) => {
  if (option.mode === 'walk') return <Footprints size={18} />
  if (option.mode === 'transit' && option.title.toLowerCase().includes('metro')) {
    return <TrainFront size={18} />
  }
  if (option.mode === 'transit') return <BusFront size={18} />
  if (option.mode === 'bixi') return <Zap size={18} />
  return <Bike size={18} />
}

function App() {
  const [stations, setStations] = useState<Station[]>(demoStations)
  const [updatedAt, setUpdatedAt] = useState(new Date().toISOString())
  const [source, setSource] = useState('demo')
  const [selectedId, setSelectedId] = useState(demoStations[0].stationId)
  const [destinationQuery, setDestinationQuery] = useState('')
  const debouncedDestinationQuery = useDebouncedValue(destinationQuery, 450)
  const [suggestions, setSuggestions] = useState<PlaceSuggestion[]>([])
  const [selectedDestination, setSelectedDestination] = useState<PlaceSuggestion | null>(null)
  const [routeOptions, setRouteOptions] = useState<RouteOption[]>([])
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null)
  const [searchLoading, setSearchLoading] = useState(false)
  const [routeLoading, setRouteLoading] = useState(false)
  const [routeError, setRouteError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)
  const [isDataPanelOpen, setIsDataPanelOpen] = useState(false)
  const [weather, setWeather] = useState<WeatherPayload | null>(null)
  const [transit, setTransit] = useState<TransitPayload | null>(null)
  const [historySummary, setHistorySummary] = useState<HistorySummary | null>(null)
  const [dataLoading, setDataLoading] = useState(false)

  const loadStations = useCallback(async () => {
    setLoading(true)
    setApiError(null)

    try {
      const response = await fetch(`${apiBaseUrl}/api/stations/live`)
      if (!response.ok) {
        throw new Error(`API returned ${response.status}`)
      }

      const payload = (await response.json()) as StationResponse
      setStations(payload.stations)
      setUpdatedAt(payload.updatedAt)
      setSource(payload.source)
      setSelectedId((current) => current || payload.stations[0]?.stationId || '')
    } catch (error) {
      setStations(demoStations)
      setUpdatedAt(new Date().toISOString())
      setSource('demo')
      setApiError(error instanceof Error ? error.message : 'API unavailable')
      setSelectedId((current) => current || demoStations[0].stationId)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadStations()
  }, [loadStations])

  useEffect(() => {
    const query = debouncedDestinationQuery.trim()
    if (query.length < 3 || selectedDestination?.label === destinationQuery) {
      setSuggestions([])
      setSearchLoading(false)
      return
    }

    const controller = new AbortController()
    setSearchLoading(true)

    fetch(`${apiBaseUrl}/api/places/search?q=${encodeURIComponent(query)}&limit=6`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Search returned ${response.status}`)
        }
        return response.json()
      })
      .then((payload: { places: PlaceSuggestion[] }) => setSuggestions(payload.places))
      .catch((error) => {
        if (error.name !== 'AbortError') {
          setSuggestions([])
        }
      })
      .finally(() => setSearchLoading(false))

    return () => controller.abort()
  }, [debouncedDestinationQuery, destinationQuery, selectedDestination?.label])

  const loadDataPanel = useCallback(async () => {
    setDataLoading(true)

    try {
      const [weatherResponse, transitResponse, historyResponse] = await Promise.all([
        fetch(`${apiBaseUrl}/api/weather/current`),
        fetch(`${apiBaseUrl}/api/transit/departures?limit=6&radius_meters=650&horizon_minutes=120`),
        fetch(`${apiBaseUrl}/api/history/summary`),
      ])

      if (weatherResponse.ok) setWeather((await weatherResponse.json()) as WeatherPayload)
      if (transitResponse.ok) setTransit((await transitResponse.json()) as TransitPayload)
      if (historyResponse.ok) setHistorySummary((await historyResponse.json()) as HistorySummary)
    } finally {
      setDataLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isDataPanelOpen) {
      void loadDataPanel()
    }
  }, [isDataPanelOpen, loadDataPanel])

  const selectDestination = async (place: PlaceSuggestion) => {
    setSelectedDestination(place)
    setDestinationQuery(place.label)
    setSuggestions([])
    setRouteOptions([])
    setSelectedRouteId(null)
    setRouteError(null)
    setRouteLoading(true)

    try {
      const response = await fetch(`${apiBaseUrl}/api/routes/options`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: place.name || place.label,
          latitude: place.latitude,
          longitude: place.longitude,
        }),
      })
      if (!response.ok) {
        throw new Error(`Route search returned ${response.status}`)
      }

      const payload = (await response.json()) as RouteResponse
      setRouteOptions(payload.options)
      setSelectedRouteId(payload.options[0]?.id ?? null)
    } catch (error) {
      setRouteError(error instanceof Error ? error.message : 'Route search failed')
    } finally {
      setRouteLoading(false)
    }
  }

  const selectedStation =
    stations.find((station) => station.stationId === selectedId) ?? stations[0]
  const selectedRoute =
    routeOptions.find((option) => option.id === selectedRouteId) ?? routeOptions[0] ?? null
  const selectedBixiStations = useMemo(() => {
    const stationMap = new Map<string, BixiRouteStation>()
    if (selectedRoute?.mode === 'bixi') {
      selectedRoute.bixiStations?.forEach((station) => {
        stationMap.set(station.stationId, station)
      })
    }

    return stationMap
  }, [selectedRoute])
  const watchlist = useMemo(
    () => [...stations].sort((a, b) => b.riskScore - a.riskScore),
    [stations],
  )

  const totals = useMemo(
    () => ({
      bikes: stations.reduce((sum, station) => sum + station.bikesAvailable, 0),
      ebikes: stations.reduce((sum, station) => sum + station.ebikesAvailable, 0),
      docks: stations.reduce((sum, station) => sum + station.docksAvailable, 0),
      risk: stations.filter(
        (station) => station.status === 'critical' || station.status === 'warning',
      ).length,
    }),
    [stations],
  )

  const trend = useMemo(() => buildTrend(selectedStation), [selectedStation])

  return (
    <div className="app-shell">
      <MapContainer
        center={[45.5076, -73.5689]}
        zoom={13}
        minZoom={11}
        scrollWheelZoom
        className="station-map"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {stations.map((station) => {
          const routeStation = selectedBixiStations.get(station.stationId)
          const isSelectedStation = selectedId === station.stationId
          return (
            <CircleMarker
              key={station.stationId}
              center={[station.latitude, station.longitude]}
              radius={routeStation ? 13 : isSelectedStation ? 11 : 7}
              pathOptions={{
                color: routeStation?.role === 'dropoff' ? '#315fd9' : '#101719',
                weight: routeStation ? 4 : isSelectedStation ? 3 : 1.5,
                fillColor: statusTone[station.status],
                fillOpacity: routeStation ? 0.96 : 0.88,
              }}
              eventHandlers={{ click: () => setSelectedId(station.stationId) }}
            >
              <Tooltip>
                <strong>{station.name}</strong>
                <br />
                {station.bikesAvailable} bikes / {station.docksAvailable} docks
                {routeStation ? (
                  <>
                    <br />
                    <em>
                      {routeStation.role === 'pickup'
                        ? 'Recommended pickup'
                        : 'Recommended dropoff'}
                    </em>
                  </>
                ) : null}
              </Tooltip>
            </CircleMarker>
          )
        })}
        {selectedRoute?.geometry.length ? (
          <Polyline
            positions={selectedRoute.geometry}
            pathOptions={{
              color: routeTone[selectedRoute.mode],
              weight: 6,
              opacity: 0.85,
            }}
          />
        ) : null}
        {selectedDestination ? (
          <CircleMarker
            center={[selectedDestination.latitude, selectedDestination.longitude]}
            radius={10}
            pathOptions={{
              color: '#101719',
              weight: 3,
              fillColor: '#315fd9',
              fillOpacity: 0.95,
            }}
          >
            <Tooltip>
              <strong>{selectedDestination.name}</strong>
              <br />
              Destination
            </Tooltip>
          </CircleMarker>
        ) : null}
        <RouteBounds route={selectedRoute} destination={selectedDestination} />
      </MapContainer>

      <div className="map-shade" aria-hidden="true" />

      <aside className="search-widget destination-panel" aria-label="Destination search">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <MapIcon size={24} />
          </div>
          <div>
            <p className="eyebrow">Start: Concordia Hall</p>
            <h1>Moogle Maps</h1>
          </div>
        </div>

        <label className="search-field">
          <Search size={18} aria-hidden="true" />
          <input
            type="search"
            placeholder="Where to?"
            value={destinationQuery}
            onChange={(event) => {
              setDestinationQuery(event.target.value)
              setSelectedDestination(null)
              setRouteOptions([])
              setSelectedRouteId(null)
              setRouteError(null)
            }}
          />
        </label>

        {searchLoading && <div className="search-status">Searching...</div>}

        {suggestions.length > 0 && (
          <div className="suggestion-list" aria-label="Destination results">
            {suggestions.map((place) => (
              <button key={place.id} type="button" onClick={() => void selectDestination(place)}>
                <MapPin size={17} />
                <span>
                  <strong>{place.name}</strong>
                  <small>{place.label}</small>
                </span>
              </button>
            ))}
          </div>
        )}

        {routeLoading && (
          <div className="route-loading">
            <RefreshCw size={18} className="is-spinning" />
            <span>Finding route options</span>
          </div>
        )}

        {routeError && <div className="route-error">{routeError}</div>}

        {routeOptions.length > 0 && (
          <div className="route-option-list" aria-label="Route options">
            {routeOptions.map((option) => (
              <button
                key={option.id}
                className={selectedRoute?.id === option.id ? 'is-selected' : ''}
                type="button"
                onClick={() => setSelectedRouteId(option.id)}
              >
                <span className="mode-icon" style={{ color: routeTone[option.mode] }}>
                  {routeIcon(option)}
                </span>
                <span>
                  <strong>
                    {option.title} · {option.durationMinutes} min
                  </strong>
                  <small>
                    {formatDistance(option.distanceMeters)} · {option.rating}/100{' '}
                    {option.ratingLabel} · {option.searchSteps} search steps
                  </small>
                </span>
              </button>
            ))}
          </div>
        )}
      </aside>

      <div className="map-actions" aria-label="Map actions">
        <span className={`source-pill ${source === 'demo' ? 'source-pill--demo' : ''}`}>
          <Database size={15} />
          {source === 'demo' ? 'Demo data' : 'Live GBFS'}
        </span>
        <button className="map-action-button" type="button" onClick={loadStations}>
          <RefreshCw size={17} className={loading ? 'is-spinning' : ''} />
          Refresh
        </button>
        <button
          className="map-action-button map-action-button--primary"
          type="button"
          onClick={() => setIsDataPanelOpen(true)}
        >
          <BarChart3 size={17} />
          Data
        </button>
      </div>

      {selectedRoute && (
        <section className="route-detail" aria-label="Selected route">
          <div className="detail-heading">
            <div>
              <p className="eyebrow">Selected route</p>
              <h2>
                {selectedRoute.title} to {selectedDestination?.name}
              </h2>
            </div>
            <span className="status-label" style={{ borderColor: routeTone[selectedRoute.mode] }}>
              {selectedRoute.rating}/100
            </span>
          </div>
          <div className="route-meta">
            <span>
              <Timer size={18} />
              {selectedRoute.durationMinutes} min
            </span>
            <span>
              <MapPin size={18} />
              {formatDistance(selectedRoute.distanceMeters)}
            </span>
            <span>
              <CircleGauge size={18} />
              {selectedRoute.ratingLabel}
            </span>
          </div>
          {selectedRoute.mode === 'bixi' && selectedRoute.bixiStations?.length ? (
            <div className="bixi-route-stations">
              {selectedRoute.bixiStations.map((station) => (
                <div className="bixi-route-station" key={`${station.role}-${station.stationId}`}>
                  <span
                    className="status-dot"
                    style={{ background: statusTone[station.status] }}
                  />
                  <div>
                    <strong>
                      {station.role === 'pickup' ? 'Pickup' : 'Dropoff'} · {station.name}
                    </strong>
                    <small>
                      {station.bikesAvailable} bikes · {station.docksAvailable} docks ·{' '}
                      {station.riskScore}/100 risk
                    </small>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          <div className="route-leg-list">
            {selectedRoute.legs.map((leg, index) => (
              <div className="route-leg" key={`${leg.label}-${index}`}>
                <span>{index + 1}</span>
                <div>
                  <strong>{leg.label}</strong>
                  <small>
                    {leg.durationMinutes} min · {formatDistance(leg.distanceMeters)}
                  </small>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="map-status" aria-label="Map status">
        <span>
          <AlertTriangle size={16} />
          {apiError ? 'API fallback' : `${stations.length} stations`}
        </span>
        <span>Updated {formatUpdatedAt(updatedAt)}</span>
      </div>

      {isDataPanelOpen && (
        <div className="data-overlay" role="dialog" aria-modal="true" aria-label="Mobility data">
          <section className="data-window">
            <header className="data-header">
              <div>
                <p className="eyebrow">Mobility telemetry</p>
                <h2>Station Data</h2>
              </div>
              <button
                className="icon-button"
                type="button"
                aria-label="Close data panel"
                onClick={() => setIsDataPanelOpen(false)}
              >
                <X size={20} />
              </button>
            </header>

            <section className="metric-grid" aria-label="Network summary">
              <div className="metric">
                <span>Stations</span>
                <strong>{stations.length}</strong>
              </div>
              <div className="metric">
                <span>Bikes</span>
                <strong>{totals.bikes}</strong>
              </div>
              <div className="metric">
                <span>Docks</span>
                <strong>{totals.docks}</strong>
              </div>
              <div className="metric metric--alert">
                <span>Risk</span>
                <strong>{totals.risk}</strong>
              </div>
            </section>

            <section className="context-grid" aria-label="Context data">
              <div className="context-card">
                <p className="eyebrow">Starting point</p>
                <strong>Concordia Hall Building</strong>
                <span>1455 De Maisonneuve Blvd. W.</span>
              </div>
              <div className="context-card">
                <p className="eyebrow">Weather</p>
                <strong>{weather ? `${Math.round(weather.current.temperatureC)}°C` : 'Loading'}</strong>
                <span>
                  {weather
                    ? `${weather.current.condition}, wind ${Math.round(weather.current.windSpeedKmh)} km/h`
                    : 'Open-Meteo current conditions'}
                </span>
              </div>
              <div className="context-card">
                <p className="eyebrow">CSV history</p>
                <strong>{historySummary?.exists ? `${historySummary.rows} rows` : 'Pending'}</strong>
                <span>
                  {historySummary?.updatedAt
                    ? `Updated ${formatUpdatedAt(historySummary.updatedAt)}`
                    : 'Auto-captures every minute'}
                </span>
              </div>
              <div className="context-card">
                <p className="eyebrow">STM schedules</p>
                <strong>{transit?.departures.length ?? 0} departures</strong>
                <span>{transit?.realtime ? 'Realtime' : 'Static GTFS schedule'}</span>
              </div>
            </section>

            <section className="data-grid">
              <div className="chart-frame">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">Selected station</p>
                    <h3>{selectedStation.name}</h3>
                  </div>
                </div>
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={trend} margin={{ top: 12, right: 8, bottom: 0, left: -24 }}>
                    <XAxis dataKey="time" tickLine={false} axisLine={false} tick={{ fontSize: 12 }} />
                    <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 12 }} />
                    <ChartTooltip />
                    <Area
                      type="monotone"
                      dataKey="bikes"
                      stroke="#16866f"
                      fill="#16866f"
                      fillOpacity={0.18}
                      strokeWidth={2.5}
                    />
                    <Area
                      type="monotone"
                      dataKey="docks"
                      stroke="#315fd9"
                      fill="#315fd9"
                      fillOpacity={0.08}
                      strokeWidth={2}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              <div className="data-table-wrap">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">Highest risk</p>
                    <h3>Station Watchlist</h3>
                  </div>
                </div>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Station</th>
                      <th>Bikes</th>
                      <th>Docks</th>
                      <th>Risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {watchlist.slice(0, 12).map((station) => (
                      <tr key={station.stationId}>
                        <td>
                          <span
                            className="status-dot"
                            style={{ backgroundColor: statusTone[station.status] }}
                            aria-hidden="true"
                          />
                          {station.name}
                        </td>
                        <td>{station.bikesAvailable}</td>
                        <td>{station.docksAvailable}</td>
                        <td>{station.riskScore}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="departures-panel" aria-label="Upcoming STM departures">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Near Concordia</p>
                  <h3>Upcoming STM Departures</h3>
                </div>
                {dataLoading && <span className="loading-pill">Loading</span>}
              </div>
              <div className="departure-list">
                {(transit?.departures ?? []).map((departure) => (
                  <div
                    className="departure-row"
                    key={`${departure.route}-${departure.stopName}-${departure.minutesUntil}`}
                  >
                    <span className="route-pill">{departure.route}</span>
                    <span>
                      <strong>{departure.headsign}</strong>
                      <small>
                        {departure.routeType} · {departure.stopName} ·{' '}
                        {departure.stopDistanceMeters} m away
                      </small>
                    </span>
                    <em>{departure.minutesUntil} min</em>
                  </div>
                ))}
              </div>
            </section>
          </section>
        </div>
      )}
    </div>
  )
}

export default App
