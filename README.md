# CDTA Local Lookup

A local-first Flask app for CDTA stop, route, and departure lookup. You choose an origin and destination using:

- browser geolocation
- a typed U.S. address
- raw latitude/longitude
- a saved nickname stored in SQLite

The app resolves both points to coordinates, finds nearby CDTA stops from the official static GTFS feed, shows routes serving those stops, highlights shared routes and direct-route candidates, and now also shows upcoming departures for each nearby stop.

## Official data sources used

- CDTA static GTFS feed: `https://www.cdta.org/schedules/google_transit.zip`
- U.S. Census Geocoder: `https://geocoding.geo.census.gov/geocoder/locations/onelineaddress`
- OpenStreetMap Nominatim reverse geocoder: `https://nominatim.openstreetmap.org/reverse`
- Optional CDTA GTFS-realtime trip updates: configure your official endpoint in `.env`

## Requirements

- Python 3.12+
- VS Code or any terminal

## Packages

Only safe, mainstream packages are used:

- `Flask`
- `requests`
- `gtfs-realtime-bindings` for optional official GTFS-realtime parsing
- `gunicorn` for Fly.io/Linux production serving

## Setup

### PowerShell

```powershell
python --version
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### Bash

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

If `python` on Windows opens the Microsoft Store instead of a real interpreter, install Python 3.12+ first, reopen the terminal, and then run the commands above.

## Environment variables

Current `.env.example` values:

```dotenv
FLASK_ENV=development
SECRET_KEY=change-me
HOST=127.0.0.1
PORT=5000
APP_STORAGE_DIR=
CDTA_GTFS_URL=https://www.cdta.org/schedules/google_transit.zip
HTTP_TIMEOUT_SECONDS=12
HTTP_USER_AGENT=cdta-local-lookup/1.0 (+local app)
NEARBY_STOP_LIMIT=5
NEARBY_STOP_RADIUS_MILES=0.75
ENABLE_REALTIME=false
CDTA_REALTIME_TRIP_UPDATES_URL=
CDTA_REALTIME_API_KEY=
CDTA_REALTIME_VEHICLE_POSITIONS_URL=
```

Notes:

- `SECRET_KEY` should be set to a stable secret for any shared or deployed environment.
- Leave `APP_STORAGE_DIR` blank for local development. When it is set, SQLite and GTFS cache files are stored under that root.
- If `ENABLE_REALTIME=false` or `CDTA_REALTIME_TRIP_UPDATES_URL` is blank, the app stays fully usable with scheduled departures only.
- If your realtime provider expects auth in a different shape than `x-api-key`, place the token directly in the configured URL or adapt the local config before deployment.

## Run the app

```powershell
.venv\Scripts\Activate.ps1
python app.py run
```

Then open `http://127.0.0.1:5000`.

The HTML search flow now uses Post/Redirect/Get:

- submitting the form redirects to a shareable `GET /results?...` URL
- refreshing the results page does not trigger a browser form-resubmission warning
- the home page remembers recent form choices and values with `localStorage` for convenience

## Refresh GTFS data

The app keeps the official CDTA GTFS zip in `data/google_transit.zip` by default.
If `APP_STORAGE_DIR` is set, the cache moves to `APP_STORAGE_DIR/data/google_transit.zip`.

To manually refresh it:

```powershell
.venv\Scripts\Activate.ps1
python app.py refresh-gtfs
```

You can also use the `Refresh GTFS` button in the web UI.

## Scheduled departures

Upcoming departures are computed locally from GTFS static files using:

- `routes.txt`
- `stops.txt`
- `trips.txt`
- `stop_times.txt`
- `calendar.txt`
- `calendar_dates.txt`

Behavior:

- GTFS times past `24:00:00` are supported correctly
- service calendars and exception dates are applied before showing a trip
- departure times are interpreted in `America/New_York`
- upcoming departures are grouped by route and limited to the next few results per stop
- the HTML page shows the server-side lookup timestamp near the top, for example `Checked at 4:00 PM on March 18, 2026`
- wait times are rendered in a human-friendly format such as `in 12 min`, `in 1 hr`, or `in 5 hr 27 min`
- each departure also shows the effective clock time, for example `in 5 min (departure time: 4:04 PM)`
- the HTML results page now focuses on origin-side routes and departures only; destination-side stop cards are not rendered

Example HTML display:

- `910 - BusPlus Red Line -> in 5 min (departure time: 4:04 PM), in 29 min (departure time: 4:28 PM)`
- `114 - Madison / Washington -> in 1 hr 5 min (departure time: 5:09 PM)`

If realtime is not configured, the UI and API clearly indicate that departures are scheduled-only.

## Optional realtime overlay

Realtime is an optional overlay, not a startup requirement.

To enable it:

1. Set `ENABLE_REALTIME=true`
2. Set `CDTA_REALTIME_TRIP_UPDATES_URL` to the official CDTA GTFS-realtime trip-updates endpoint
3. Set `CDTA_REALTIME_API_KEY` only if your official endpoint requires it

When enabled and reachable:

- scheduled departures stay as the fallback
- matching trip updates can replace scheduled departure times
- API responses mark each departure as `scheduled` or `realtime`

If the feed is unavailable or not configured, the app falls back gracefully to scheduled departures.

## JSON API

The app now includes Shortcut-friendly JSON endpoints:

- `GET /api/health`
- `GET /api/saved-locations`
- `GET /api/lookup`
- `POST /api/lookup`

API routes always return JSON, including validation errors.

### Health example

```http
GET /api/health
```

Example response:

```json
{
  "status": "ok",
  "timestamp": "2026-03-19T08:00:00-04:00"
}
```

### Saved locations example

```http
GET /api/saved-locations
```

### Shortcut-friendly GET lookup

Supported query params:

- `origin_lat`
- `origin_lon`
- `origin_query`
- `origin_saved`
- `destination_lat`
- `destination_lon`
- `destination_query`
- `destination_saved`
- `max_stops`
- `max_departures_per_route`

Example GET request:

```text
http://127.0.0.1:5000/api/lookup?origin_lat=42.6526&origin_lon=-73.7562&destination_query=Crossgates%20Mall%20Albany%20NY&max_stops=1&max_departures_per_route=2
```

### POST lookup

Send JSON:

```json
{
  "origin": {
    "mode": "current",
    "latitude": 42.6526,
    "longitude": -73.7562
  },
  "destination": {
    "mode": "typed",
    "query": "Crossgates Mall Albany NY"
  },
  "max_stops": 1,
  "max_departures_per_route": 2
}
```

Supported location modes:

- `current` with `latitude` and `longitude`
- `typed` with `query`
- `saved` with either `saved_id` or `nickname`

Example response shape:

```json
{
  "origin": {
    "label": "Coordinates 42.652600, -73.756200",
    "source_type": "current",
    "nickname": null,
    "saved_location_id": null,
    "latitude": 42.6526,
    "longitude": -73.7562
  },
  "destination": {
    "label": "Crossgates Mall Albany NY",
    "source_type": "typed",
    "nickname": null,
    "saved_location_id": null,
    "latitude": 42.6881,
    "longitude": -73.8498
  },
  "origin_nearest_stops": [
    {
      "stop_id": "07216",
      "stop_name": "Madison Ave & Swan St",
      "distance_miles": 0.09,
      "within_radius": true,
      "departure_source_note": "Scheduled departures.",
      "routes": [
        {
          "route_id": "910",
          "short_name": "910",
          "long_name": "BusPlus Red Line",
          "display_name": "910 - BusPlus Red Line",
          "headsign": "Downtown Albany",
          "next_departures": [
            {
              "trip_id": "trip-1",
              "headsign": "Downtown Albany",
              "scheduled_departure": "2026-03-19T08:05:00-04:00",
              "realtime_departure": null,
              "effective_departure": "2026-03-19T08:05:00-04:00",
              "minutes": 5,
              "display_wait": "in 5 min",
              "display_departure_time": "8:05 AM",
              "display_summary": "in 5 min (departure time: 8:05 AM)",
              "source": "scheduled",
              "delay_seconds": null
            }
          ]
        }
      ]
    }
  ],
  "destination_nearest_stops": [],
  "shared_routes": [],
  "direct_candidates": [],
  "realtime_used": false,
  "checked_at_display": "Checked at 8:00 AM on March 19, 2026",
  "destination_serviceable": true,
  "destination_service_message": null,
  "generated_at": "2026-03-19T08:00:00-04:00",
  "note": "Nearby stops and shared routes are a helpful shortlist, but this MVP does not guarantee a full door-to-door itinerary. Direct-route candidates are heuristic matches based on GTFS stop order."
}
```

## Geocoding behavior

Typed input keeps the original safe behavior:

- raw `lat,lon` is used directly
- typed U.S. addresses go through the U.S. Census Geocoder over plain HTTP requests
- browser geolocation attempts a reverse lookup through OpenStreetMap Nominatim so the UI can show a nearby address instead of coordinates alone
- no geocoder wrapper libraries are added

## Saved nicknames

- Saved locations are stored in SQLite at `instance/saved_locations.sqlite` by default.
- If `APP_STORAGE_DIR` is set, the database moves to `APP_STORAGE_DIR/instance/saved_locations.sqlite`.
- Nicknames are unique case-insensitively, so `Home` and `home` cannot both exist.
- API lookup can resolve saved locations by either numeric `saved_id` or nickname.

## Security and deployment notes

- API routes are read-only except for the existing saved-location HTML form actions.
- For public deployment, protect the saved-location write routes behind auth or private network access.
- Do not commit real secrets into `.env`.
- `SECRET_KEY` should come from environment for deployed environments.
- `debug` mode should stay off in production.

## Fly.io deployment

This repo now includes the minimum production files for a simple Fly.io deployment:

- `gunicorn` in `requirements.txt`
- `wsgi.py` as the WSGI entrypoint
- `Dockerfile` for a repeatable Linux build
- `fly.toml` configured for one low-cost machine with a mounted volume
- `APP_STORAGE_DIR=/data` support so SQLite and GTFS cache persist on the Fly volume

Important deployment note:

- Keep this app at exactly one Fly machine. SQLite and Fly volumes are local-file storage, so running multiple machines can split writes and cache state.

### 1. Install and log in to Fly

Install `flyctl` from `https://fly.io/docs/flyctl/install/`, then log in:

```powershell
fly auth login
```

### 2. Choose an app name

Edit `fly.toml` and replace:

```toml
app = "replace-with-your-fly-app-name"
```

Or let `fly launch` set the name for you.

### 3. Initialize the Fly app

From this project folder:

```powershell
fly launch --name <your-fly-app-name> --region yul --no-deploy
```

This repo already includes `fly.toml`, so `fly launch` should keep the existing shape and register the app.

### 4. Create the persistent volume

Saved locations and GTFS cache persistence depend on Fly volume storage:

```powershell
fly volumes create app_data --region yul --size 3
```

### 5. Set the production secret

Set a real secret key before deploying:

```powershell
fly secrets set SECRET_KEY="replace-with-a-long-random-secret"
```

The app now fails clearly in production if `SECRET_KEY` is missing.

### 6. Deploy

```powershell
fly deploy
```

### 7. Check logs

```powershell
fly logs
```

### Fly storage behavior

- `APP_STORAGE_DIR=/data` is configured in `fly.toml`
- SQLite will live at `/data/instance/saved_locations.sqlite`
- GTFS cache will live at `/data/data/google_transit.zip`
- Without the Fly volume, saved locations and GTFS cache would be lost on machine replacement or deploy

## Testing

```powershell
.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
```

Coverage now includes:

- latitude/longitude parsing and validation
- case-insensitive nickname uniqueness and nickname lookup
- GTFS time parsing including values past `24:00:00`
- service-calendar activation including `calendar_dates` overrides
- wait-time formatting and departure clock formatting
- next departure computation
- no-results departure behavior
- JSON API happy paths and validation errors
- HTML checked-at rendering and Post/Redirect/Get results behavior

## Project structure

```text
cdta/
  app.py
  requirements.txt
  .env.example
  README.md
  data/
  instance/
  services/
  static/
  templates/
  tests/
  utils/
```

## Notes about scope

Implemented now:

- browser geolocation with clear success/error UI and nearby address translation when available
- typed address lookup via direct Census Geocoder HTTP calls
- raw latitude/longitude input
- saved nickname CRUD with SQLite
- official CDTA GTFS download/cache/load flow
- nearby origin and destination stops
- routes serving nearby stops
- shared-route highlighting
- direct-route heuristic based on GTFS stop order
- scheduled upcoming departures per nearby stop
- human-friendly wait times and departure clock times in the HTML UI
- checked-at timestamp at the top of results
- Post/Redirect/Get results URLs to avoid browser resubmission prompts
- localStorage-backed last-used form memory on the home page
- optional GTFS-realtime trip-update overlay
- Shortcut-friendly JSON API
- manual GTFS refresh via CLI and web button

Still intentionally out of scope:

- full itinerary planning with transfers, time windows, and schedule-aware ranking
- automatic public deployment hardening beyond the documented guardrails
