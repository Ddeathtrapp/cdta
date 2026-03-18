from __future__ import annotations

import csv
import io
import logging
import os
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import TZPATH, ZoneInfo, ZoneInfoNotFoundError, reset_tzpath

import requests


LOGGER = logging.getLogger(__name__)
WEEKDAY_FIELD_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _load_service_timezone() -> ZoneInfo:
    try:
        return ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        candidate_paths: list[str] = []
        candidate_dirs = [
            Path(sys.prefix) / "Lib" / "zoneinfo",
            Path(sys.base_prefix) / "Lib" / "zoneinfo",
            Path(sys.executable).resolve().parent.parent / "Lib" / "zoneinfo",
            Path(os.environ.get("ProgramFiles", "")) / "Git" / "mingw64" / "share" / "zoneinfo",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Git" / "mingw64" / "share" / "zoneinfo",
        ]
        for path in candidate_dirs:
            zone_file = path / "America" / "New_York"
            if not zone_file.exists():
                continue
            try:
                if zone_file.read_bytes()[:4] == b"TZif":
                    candidate_paths.append(str(path))
            except OSError:
                continue

        if candidate_paths:
            reset_tzpath([*candidate_paths, *TZPATH])
            return ZoneInfo("America/New_York")
        raise


SERVICE_TIMEZONE = _load_service_timezone()


class GTFSDownloadError(RuntimeError):
    """Raised when the official GTFS feed cannot be downloaded."""


class GTFSLoadError(RuntimeError):
    """Raised when the GTFS feed cannot be parsed."""


def parse_gtfs_time_to_seconds(raw_value: str) -> int:
    text = str(raw_value or "").strip()
    if not text:
        raise ValueError("GTFS time value is required.")

    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid GTFS time value: {text!r}")

    hours, minutes, seconds = (int(part) for part in parts)
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"Invalid GTFS time value: {text!r}")

    return (hours * 3600) + (minutes * 60) + seconds


def parse_service_date(raw_value: str) -> date:
    return datetime.strptime(raw_value.strip(), "%Y%m%d").date()


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
    service_id: str
    headsign: str
    direction_id: str


@dataclass(frozen=True)
class StopTimeInfo:
    trip_id: str
    stop_id: str
    stop_sequence: int
    arrival_seconds: int
    departure_seconds: int


@dataclass(frozen=True)
class ServiceCalendar:
    service_id: str
    start_date: date
    end_date: date
    weekdays: tuple[bool, bool, bool, bool, bool, bool, bool]

    def is_active_on(self, service_date: date) -> bool:
        if service_date < self.start_date or service_date > self.end_date:
            return False
        return self.weekdays[service_date.weekday()]


@dataclass(frozen=True)
class GTFSData:
    feed_path: Path
    loaded_at: datetime
    routes: dict[str, RouteInfo]
    stops: dict[str, StopInfo]
    trips: dict[str, TripInfo]
    service_calendars: dict[str, ServiceCalendar]
    service_exceptions: dict[str, dict[date, bool]]
    stop_routes: dict[str, set[str]]
    route_trip_ids: dict[str, list[str]]
    trip_stop_sequences: dict[str, list[tuple[int, str]]]
    stop_times_by_stop: dict[str, list[StopTimeInfo]]
    trip_stop_times: dict[str, list[StopTimeInfo]]
    trip_stop_ids_by_sequence: dict[str, dict[int, str]]

    def is_service_active(self, service_id: str, service_date: date) -> bool:
        overrides = self.service_exceptions.get(service_id, {})
        if service_date in overrides:
            return overrides[service_date]

        calendar = self.service_calendars.get(service_id)
        if calendar is None:
            return False
        return calendar.is_active_on(service_date)

    @staticmethod
    def service_date_to_datetime(service_date: date, seconds_after_midnight: int) -> datetime:
        start_of_day = datetime.combine(service_date, time.min, tzinfo=SERVICE_TIMEZONE)
        return start_of_day + timedelta(seconds=seconds_after_midnight)


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
            updated_at = datetime.fromtimestamp(
                self.feed_path.stat().st_mtime,
                tz=SERVICE_TIMEZONE,
            ).isoformat(timespec="seconds")
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
                service_calendars = self._load_calendar(archive)
                service_exceptions = self._load_calendar_dates(archive)
                trips, route_trip_ids = self._load_trips(archive)
                (
                    stop_routes,
                    trip_stop_sequences,
                    stop_times_by_stop,
                    trip_stop_times,
                    trip_stop_ids_by_sequence,
                ) = self._load_stop_times(archive, trips)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile, csv.Error) as exc:
            raise GTFSLoadError("Could not parse the GTFS feed.") from exc

        return GTFSData(
            feed_path=feed_path,
            loaded_at=datetime.now(tz=SERVICE_TIMEZONE),
            routes=routes,
            stops=stops,
            trips=trips,
            service_calendars=service_calendars,
            service_exceptions=service_exceptions,
            stop_routes=stop_routes,
            route_trip_ids=route_trip_ids,
            trip_stop_sequences=trip_stop_sequences,
            stop_times_by_stop=stop_times_by_stop,
            trip_stop_times=trip_stop_times,
            trip_stop_ids_by_sequence=trip_stop_ids_by_sequence,
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

    def _load_calendar(self, archive: zipfile.ZipFile) -> dict[str, ServiceCalendar]:
        calendars: dict[str, ServiceCalendar] = {}
        for row in self._iter_csv_rows(archive, "calendar.txt"):
            service_id = row["service_id"]
            weekdays = tuple((row.get(field_name, "0").strip() == "1") for field_name in WEEKDAY_FIELD_NAMES)
            calendars[service_id] = ServiceCalendar(
                service_id=service_id,
                start_date=parse_service_date(row["start_date"]),
                end_date=parse_service_date(row["end_date"]),
                weekdays=weekdays,
            )
        return calendars

    def _load_calendar_dates(self, archive: zipfile.ZipFile) -> dict[str, dict[date, bool]]:
        service_exceptions: dict[str, dict[date, bool]] = {}
        for row in self._iter_csv_rows(archive, "calendar_dates.txt"):
            service_id = row["service_id"]
            service_date = parse_service_date(row["date"])
            exception_type = row.get("exception_type", "").strip()
            if exception_type not in {"1", "2"}:
                continue
            service_exceptions.setdefault(service_id, {})[service_date] = exception_type == "1"
        return service_exceptions

    def _load_trips(self, archive: zipfile.ZipFile) -> tuple[dict[str, TripInfo], dict[str, list[str]]]:
        trips: dict[str, TripInfo] = {}
        route_trip_ids: dict[str, list[str]] = {}

        for row in self._iter_csv_rows(archive, "trips.txt"):
            trip_id = row["trip_id"]
            route_id = row["route_id"]
            trip = TripInfo(
                trip_id=trip_id,
                route_id=route_id,
                service_id=row["service_id"],
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
    ) -> tuple[
        dict[str, set[str]],
        dict[str, list[tuple[int, str]]],
        dict[str, list[StopTimeInfo]],
        dict[str, list[StopTimeInfo]],
        dict[str, dict[int, str]],
    ]:
        stop_routes: dict[str, set[str]] = {}
        trip_stop_sequences: dict[str, list[tuple[int, str]]] = {}
        stop_times_by_stop: dict[str, list[StopTimeInfo]] = {}
        trip_stop_times: dict[str, list[StopTimeInfo]] = {}
        trip_stop_ids_by_sequence: dict[str, dict[int, str]] = {}

        for row in self._iter_csv_rows(archive, "stop_times.txt"):
            trip_id = row["trip_id"]
            trip = trips.get(trip_id)
            if trip is None:
                continue

            stop_id = row["stop_id"]
            sequence = int(row["stop_sequence"])
            arrival_seconds = parse_gtfs_time_to_seconds(row.get("arrival_time") or row.get("departure_time") or "")
            departure_seconds = parse_gtfs_time_to_seconds(row.get("departure_time") or row.get("arrival_time") or "")
            stop_time = StopTimeInfo(
                trip_id=trip_id,
                stop_id=stop_id,
                stop_sequence=sequence,
                arrival_seconds=arrival_seconds,
                departure_seconds=departure_seconds,
            )

            stop_routes.setdefault(stop_id, set()).add(trip.route_id)
            stop_times_by_stop.setdefault(stop_id, []).append(stop_time)
            trip_stop_times.setdefault(trip_id, []).append(stop_time)
            trip_stop_sequences.setdefault(trip_id, []).append((sequence, stop_id))
            trip_stop_ids_by_sequence.setdefault(trip_id, {})[sequence] = stop_id

        for stop_sequence_list in trip_stop_sequences.values():
            stop_sequence_list.sort(key=lambda item: item[0])

        for stop_times in stop_times_by_stop.values():
            stop_times.sort(key=lambda item: (item.departure_seconds, item.trip_id, item.stop_sequence))

        for stop_times in trip_stop_times.values():
            stop_times.sort(key=lambda item: item.stop_sequence)

        return (
            stop_routes,
            trip_stop_sequences,
            stop_times_by_stop,
            trip_stop_times,
            trip_stop_ids_by_sequence,
        )

    @staticmethod
    def _iter_csv_rows(archive: zipfile.ZipFile, member_name: str) -> csv.DictReader:
        with archive.open(member_name) as handle:
            text_stream = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text_stream)
            for row in reader:
                yield row
