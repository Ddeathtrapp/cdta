# CDTA Local Lookup

A local-first Flask app for simple CDTA bus route lookup. You choose an origin and destination using:

- browser geolocation
- a typed U.S. address
- raw latitude/longitude
- a saved nickname stored in SQLite

The app resolves both points to coordinates, finds nearby CDTA stops from the official static GTFS feed, shows routes serving those stops, and highlights direct-route candidates when the stop order suggests a likely one-seat ride.

## Official data sources used

- CDTA static GTFS feed: `https://www.cdta.org/schedules/google_transit.zip`
- U.S. Census Geocoder: `https://geocoding.geo.census.gov/geocoder/locations/onelineaddress`

## Requirements

- Python 3.12+
- VS Code or any terminal

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

## Run the app

```powershell
.venv\Scripts\Activate.ps1
python app.py run
```

Then open `http://127.0.0.1:5000`.

## Refresh GTFS data

The app keeps the official CDTA GTFS zip in `data/google_transit.zip`.

To manually refresh it:

```powershell
.venv\Scripts\Activate.ps1
python app.py refresh-gtfs
```

You can also use the `Refresh GTFS` button in the web UI.

## How saved nicknames work

- Saved locations are stored in SQLite at `instance/saved_locations.sqlite`.
- Nicknames are unique case-insensitively, so `Home` and `home` cannot both exist.
- Each saved record includes:
  - `id`
  - `nickname`
  - `original_label`
  - `latitude`
  - `longitude`
  - `created_at`
  - `updated_at`
- You can create saved locations from:
  - the Saved Locations page
  - the search results page after resolving an origin or destination

## Testing

```powershell
.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
```

The included tests cover:

- latitude/longitude parsing and validation
- nickname uniqueness in SQLite
- nearest-stop selection logic

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

- browser geolocation with clear success/error UI
- typed address lookup via direct Census Geocoder HTTP calls
- raw latitude/longitude input
- saved nickname CRUD with SQLite
- official CDTA GTFS download/cache/load flow
- nearby origin and destination stops
- routes serving nearby stops
- shared-route highlighting
- direct-route heuristic based on GTFS stop order
- manual GTFS refresh via CLI and web button

Scaffolded for later:

- official CDTA realtime integration through environment-driven settings
- full itinerary planning with transfers, time windows, and schedule-aware ranking
