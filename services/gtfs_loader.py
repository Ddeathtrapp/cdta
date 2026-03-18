from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests


LOGGER = logging.getLogger(__name__)


class GTFSDownloadError(RuntimeError):
    """Raised when the official GTFS feed cannot be downloaded."""


class GTFSLoadError(RuntimeError):
    """Raised when the GTFS feed cannot be parsed."""


@dataclass(frozen=True)
class RouteInfo:
    route_id: str
    short_name: str
    long_name: str
    description: str
    url: str
    color: str
    text_color: str
    sort_order: int

    @property
    def display_name(self) -> str:
        if self.short_name and self.long_name:
            return f"{self.short_name} - {self.long_name}"
        return self.short_name or self.long_name or self.route_id


@dataclass(frozen=True)
class StopInfo:
    stop_id: str
    code: str
    name: str
    latitude: float
    longitude: float
    url: str


@dataclass(frozen=True)
class TripInfo:
    trip_id: str
    route_id: str
    headsign: str
    direction_id: str


@dataclass(frozen=True)
class GTFSData:
    feed_path: Path
    loaded_at: datetime
    routes: dict[str, RouteInfo]
    stops: dict[str, StopInfo]
    trips: dict[str, TripInfo]
    stop_routes: dict[str, set[str]]
    route_trip_ids: dict[str, list[str]]
    trip_stop_sequences: dict[str, list[tuple[int, str]]]


class GTFSLoader:
    def __init__(
        self,
        *,
        feed_url: str,
        data_dir: Path,
        timeout_seconds: float,
        user_agent: str,
    ) -> None:
        self.feed_url = feed_url
        self.data_dir = data_dir
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.feed_path = self.data_dir / "google_transit.zip"
        self._cached_data: GTFSData | None = None
        self._cached_mtime: float | None = None

    def get_feed_status(self) -> dict[str, str | bool | None]:
        exists = self.feed_path.exists()
        updated_at = None
        if exists:
            updated_at = datetime.fromtimestamp(self.feed_path.stat().st_mtime).isoformat(timespec="seconds")
        return {
            "exists": exists,
            "path": str(self.feed_path),
            "updated_at": updated_at,
        }

    def ensure_feed(self) -> Path:
        if self.feed_path.exists():
            return self.feed_path
        return self.refresh_feed()

    def refresh_feed(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.feed_path.with_suffix(".tmp")

        LOGGER.info("Downloading CDTA GTFS feed from %s", self.feed_url)
        try:
            with requests.Session() as session:
                session.headers.update({"User-Agent": self.user_agent})
                with session.get(self.feed_url, timeout=self.timeout_seconds, stream=True) as response:
                    response.raise_for_status()
                    with temp_path.open("wb") as handle:
                        for chunk in response.iter_content(chunk_size=1024 * 64):
                            if chunk:
                                handle.write(chunk)
        except requests.RequestException as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise GTFSDownloadError("Could not download the official CDTA GTFS feed.") from exc

        temp_path.replace(self.feed_path)
        self._cached_data = None
        self._cached_mtime = None
        LOGGER.info("Saved GTFS feed to %s", self.feed_path)
        return self.feed_path

    def load(self) -> GTFSData:
        feed_path = self.ensure_feed()
        mtime = feed_path.stat().st_mtime

        if self._cached_data is not None and self._cached_mtime == mtime:
            return self._cached_data

        data = self._parse_feed(feed_path)
        self._cached_data = data
        self._cached_mtime = mtime
        return data

    def _parse_feed(self, feed_path: Path) -> GTFSData:
        LOGGER.info("Loading GTFS feed from %s", feed_path)

        try:
            with zipfile.ZipFile(feed_path) as archive:
                routes = self._load_routes(archive)
                stops = self._load_stops(archive)
                trips, route_trip_ids = self._load_trips(archive)
                stop_routes, trip_stop_sequences = self._load_stop_times(archive, trips)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile, csv.Error) as exc:
            raise GTFSLoadError("Could not parse the GTFS feed.") from exc

        return GTFSData(
            feed_path=feed_path,
            loaded_at=datetime.now(),
            routes=routes,
            stops=stops,
            trips=trips,
            stop_routes=stop_routes,
            route_trip_ids=route_trip_ids,
            trip_stop_sequences=trip_stop_sequences,
        )

    def _load_routes(self, archive: zipfile.ZipFile) -> dict[str, RouteInfo]:
        routes: dict[str, RouteInfo] = {}
        for row in self._iter_csv_rows(archive, "routes.txt"):
            route_id = row["route_id"]
            sort_order = int(row.get("route_sort_order") or 0)
            routes[route_id] = RouteInfo(
                route_id=route_id,
                short_name=row.get("route_short_name", "").strip(),
                long_name=row.get("route_long_name", "").strip(),
                description=row.get("route_desc", "").strip(),
                url=row.get("route_url", "").strip(),
                color=row.get("route_color", "").strip(),
                text_color=row.get("route_text_color", "").strip(),
                sort_order=sort_order,
            )
        return routes

    def _load_stops(self, archive: zipfile.ZipFile) -> dict[str, StopInfo]:
        stops: dict[str, StopInfo] = {}
        for row in self._iter_csv_rows(archive, "stops.txt"):
            location_type = (row.get("location_type") or "0").strip()
            if location_type not in {"", "0"}:
                continue
            stop_id = row["stop_id"]
            stops[stop_id] = StopInfo(
                stop_id=stop_id,
                code=row.get("stop_code", "").strip(),
                name=row.get("stop_name", "").strip(),
                latitude=float(row["stop_lat"]),
                longitude=float(row["stop_lon"]),
                url=row.get("stop_url", "").strip(),
            )
        return stops

    def _load_trips(self, archive: zipfile.ZipFile) -> tuple[dict[str, TripInfo], dict[str, list[str]]]:
        trips: dict[str, TripInfo] = {}
        route_trip_ids: dict[str, list[str]] = {}

        for row in self._iter_csv_rows(archive, "trips.txt"):
            trip_id = row["trip_id"]
            route_id = row["route_id"]
            trip = TripInfo(
                trip_id=trip_id,
                route_id=route_id,
                headsign=row.get("trip_headsign", "").strip(),
                direction_id=row.get("direction_id", "").strip(),
            )
            trips[trip_id] = trip
            route_trip_ids.setdefault(route_id, []).append(trip_id)

        return trips, route_trip_ids

    def _load_stop_times(
        self,
        archive: zipfile.ZipFile,
        trips: dict[str, TripInfo],
    ) -> tuple[dict[str, set[str]], dict[str, list[tuple[int, str]]]]:
        stop_routes: dict[str, set[str]] = {}
        trip_stop_sequences: dict[str, list[tuple[int, str]]] = {}

        for row in self._iter_csv_rows(archive, "stop_times.txt"):
            trip_id = row["trip_id"]
            trip = trips.get(trip_id)
            if trip is None:
                continue

            stop_id = row["stop_id"]
            sequence = int(row["stop_sequence"])
            stop_routes.setdefault(stop_id, set()).add(trip.route_id)
            trip_stop_sequences.setdefault(trip_id, []).append((sequence, stop_id))

        for stop_sequence_list in trip_stop_sequences.values():
            stop_sequence_list.sort(key=lambda item: item[0])

        return stop_routes, trip_stop_sequences

    @staticmethod
    def _iter_csv_rows(archive: zipfile.ZipFile, member_name: str) -> csv.DictReader:
        with archive.open(member_name) as handle:
            text_stream = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text_stream)
            for row in reader:
                yield row
