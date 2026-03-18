from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from services.gtfs_loader import (
    GTFSData,
    GTFSDownloadError,
    GTFSLoadError,
    GTFSLoader,
    RouteInfo,
    SERVICE_TIMEZONE,
    StopInfo,
)
from services.realtime import GTFSRealtimeService, RealtimeStopUpdate
from utils.geo import format_coordinates, haversine_miles


LOOKBACK_SERVICE_DAYS = 1
LOOKAHEAD_SERVICE_DAYS = 7
DEPARTURE_GRACE_SECONDS = 45
DEFAULT_OVERALL_DEPARTURE_LIMIT = 8


def format_wait_time(minutes: int) -> str:
    normalized_minutes = max(0, int(minutes))
    if normalized_minutes == 0:
        return "due now"
    if normalized_minutes < 60:
        return f"in {normalized_minutes} min"

    hours, remainder_minutes = divmod(normalized_minutes, 60)
    if remainder_minutes == 0:
        return f"in {hours} hr"
    return f"in {hours} hr {remainder_minutes} min"


def format_departure_clock_time(departure_time: datetime) -> str:
    localized = departure_time.astimezone(SERVICE_TIMEZONE)
    hour = localized.hour % 12 or 12
    return f"{hour}:{localized.minute:02d} {'AM' if localized.hour < 12 else 'PM'}"


def format_checked_at_text(checked_at: datetime) -> str:
    localized = checked_at.astimezone(SERVICE_TIMEZONE)
    return f"Checked at {format_departure_clock_time(localized)} on {localized.strftime('%B')} {localized.day}, {localized.year}"


@dataclass(frozen=True)
class ResolvedLocation:
    source_type: str
    label: str
    latitude: float
    longitude: float
    nickname: str | None = None
    saved_location_id: int | None = None

    @property
    def source_label(self) -> str:
        labels = {
            "current": "Current location",
            "typed": "Typed location",
            "saved": "Saved nickname",
        }
        return labels.get(self.source_type, self.source_type.title())

    @property
    def coordinates_text(self) -> str:
        return format_coordinates(self.latitude, self.longitude)


@dataclass(frozen=True)
class DepartureInfo:
    route: RouteInfo
    trip_id: str
    headsign: str
    scheduled_departure: datetime
    realtime_departure: datetime | None
    minutes_until_departure: int
    source: str
    delay_seconds: int | None = None

    @property
    def effective_departure(self) -> datetime:
        return self.realtime_departure or self.scheduled_departure

    @property
    def relative_label(self) -> str:
        return self.display_wait

    @property
    def display_wait(self) -> str:
        return format_wait_time(self.minutes_until_departure)

    @property
    def display_departure_time(self) -> str:
        return format_departure_clock_time(self.effective_departure)

    @property
    def display_summary(self) -> str:
        return f"{self.display_wait} (departure time: {self.display_departure_time})"


@dataclass(frozen=True)
class RouteDepartureGroup:
    route: RouteInfo
    departures: list[DepartureInfo]

    @property
    def headsign(self) -> str | None:
        headsigns = {departure.headsign for departure in self.departures if departure.headsign}
        if len(headsigns) == 1:
            return next(iter(headsigns))
        return None

    @property
    def summary_text(self) -> str:
        return ", ".join(departure.display_summary for departure in self.departures)

    @property
    def has_realtime(self) -> bool:
        return any(departure.source == "realtime" for departure in self.departures)


@dataclass(frozen=True)
class NearbyStopResult:
    stop: StopInfo
    distance_miles: float
    routes: list[RouteInfo]
    within_radius: bool
    route_departures: list[RouteDepartureGroup] = field(default_factory=list)

    @property
    def has_departures(self) -> bool:
        return any(group.departures for group in self.route_departures)

    @property
    def departures_summary_label(self) -> str:
        if not self.has_departures:
            return "No upcoming departures found at this stop."
        if any(group.has_realtime for group in self.route_departures):
            return "Realtime-adjusted when available."
        return "Scheduled departures."


@dataclass(frozen=True)
class DirectRouteCandidate:
    route: RouteInfo
    origin_stop: StopInfo
    destination_stop: StopInfo
    origin_distance_miles: float
    destination_distance_miles: float
    trip_headsign: str
    stop_gap: int


@dataclass(frozen=True)
class TransitLookupResult:
    origin_stops: list[NearbyStopResult]
    destination_stops: list[NearbyStopResult]
    shared_routes: list[RouteInfo]
    direct_candidates: list[DirectRouteCandidate]
    note: str
    generated_at: datetime
    realtime_used: bool
    destination_serviceable: bool
    destination_service_message: str | None

    @property
    def departure_source_note(self) -> str:
        if self.realtime_used:
            return "Upcoming departures are realtime-adjusted when the CDTA trip-updates feed has matching data."
        return "Upcoming departures are scheduled-only right now."

    @property
    def generated_at_text(self) -> str:
        return self.generated_at.isoformat(timespec="seconds")

    @property
    def checked_at_text(self) -> str:
        return format_checked_at_text(self.generated_at)


def sort_routes(route_ids: set[str], routes: dict[str, RouteInfo]) -> list[RouteInfo]:
    return sorted(
        (routes[route_id] for route_id in route_ids if route_id in routes),
        key=lambda route: (route.sort_order, route.short_name or route.long_name or route.route_id),
    )


def find_nearest_stops(
    dataset: GTFSData,
    latitude: float,
    longitude: float,
    *,
    limit: int,
    radius_miles: float,
) -> list[NearbyStopResult]:
    measured: list[tuple[float, StopInfo]] = []
    for stop in dataset.stops.values():
        distance = haversine_miles(latitude, longitude, stop.latitude, stop.longitude)
        measured.append((distance, stop))

    measured.sort(key=lambda item: item[0])
    if not measured:
        return []

    within_radius = [(distance, stop) for distance, stop in measured if distance <= radius_miles]
    selected = within_radius[:limit] if within_radius else measured[:limit]

    results: list[NearbyStopResult] = []
    for distance, stop in selected:
        route_ids = dataset.stop_routes.get(stop.stop_id, set())
        results.append(
            NearbyStopResult(
                stop=stop,
                distance_miles=distance,
                routes=sort_routes(route_ids, dataset.routes),
                within_radius=distance <= radius_miles,
            )
        )
    return results


class CDTAService:
    def __init__(
        self,
        loader: GTFSLoader,
        *,
        nearby_stop_limit: int,
        nearby_stop_radius_miles: float,
        realtime_service: GTFSRealtimeService | None = None,
    ) -> None:
        self.loader = loader
        self.nearby_stop_limit = nearby_stop_limit
        self.nearby_stop_radius_miles = nearby_stop_radius_miles
        self.realtime_service = realtime_service

    def get_feed_status(self) -> dict[str, str | bool | None]:
        return self.loader.get_feed_status()

    def refresh_feed(self) -> str:
        return str(self.loader.refresh_feed())

    def get_upcoming_departures_for_stop(
        self,
        stop_id: str,
        now: datetime | None = None,
        *,
        limit_per_route: int = 2,
        overall_limit: int = DEFAULT_OVERALL_DEPARTURE_LIMIT,
    ) -> list[RouteDepartureGroup]:
        dataset = self.loader.load()
        current_time = self._normalize_now(now)
        realtime_updates = self._load_realtime_updates(dataset)
        return self._get_upcoming_departures_for_stop(
            dataset,
            stop_id,
            current_time,
            limit_per_route=limit_per_route,
            overall_limit=overall_limit,
            realtime_updates=realtime_updates,
        )

    def lookup(
        self,
        origin: ResolvedLocation,
        destination: ResolvedLocation,
        *,
        now: datetime | None = None,
        max_stops: int | None = None,
        max_departures_per_route: int = 2,
    ) -> TransitLookupResult:
        dataset = self.loader.load()
        current_time = self._normalize_now(now)
        realtime_updates = self._load_realtime_updates(dataset)
        stop_limit = max_stops or self.nearby_stop_limit

        origin_stops = self._attach_departures(
            dataset,
            find_nearest_stops(
                dataset,
                origin.latitude,
                origin.longitude,
                limit=stop_limit,
                radius_miles=self.nearby_stop_radius_miles,
            ),
            current_time,
            max_departures_per_route=max_departures_per_route,
            realtime_updates=realtime_updates,
        )
        destination_stops = self._attach_departures(
            dataset,
            find_nearest_stops(
                dataset,
                destination.latitude,
                destination.longitude,
                limit=stop_limit,
                radius_miles=self.nearby_stop_radius_miles,
            ),
            current_time,
            max_departures_per_route=max_departures_per_route,
            realtime_updates=realtime_updates,
        )

        origin_route_ids = {route.route_id for stop in origin_stops for route in stop.routes}
        destination_route_ids = {route.route_id for stop in destination_stops for route in stop.routes}
        shared_route_ids = origin_route_ids & destination_route_ids
        realtime_used = any(
            departure.source == "realtime"
            for stop_result in origin_stops + destination_stops
            for route_group in stop_result.route_departures
            for departure in route_group.departures
        )
        destination_serviceable = any(stop.within_radius for stop in destination_stops)
        destination_service_message = None
        if not destination_serviceable:
            destination_service_message = "Destination appears outside the CDTA service area."

        return TransitLookupResult(
            origin_stops=origin_stops,
            destination_stops=destination_stops,
            shared_routes=sort_routes(shared_route_ids, dataset.routes),
            direct_candidates=self._find_direct_candidates(dataset, origin_stops, destination_stops, shared_route_ids),
            note=(
                "Nearby stops and shared routes are a helpful shortlist, but this MVP does not guarantee a full "
                "door-to-door itinerary. Direct-route candidates are heuristic matches based on GTFS stop order."
            ),
            generated_at=current_time,
            realtime_used=realtime_used,
            destination_serviceable=destination_serviceable,
            destination_service_message=destination_service_message,
        )

    def _attach_departures(
        self,
        dataset: GTFSData,
        stop_results: list[NearbyStopResult],
        now: datetime,
        *,
        max_departures_per_route: int,
        realtime_updates: dict[tuple[str, str], RealtimeStopUpdate],
    ) -> list[NearbyStopResult]:
        hydrated: list[NearbyStopResult] = []
        for stop_result in stop_results:
            hydrated.append(
                NearbyStopResult(
                    stop=stop_result.stop,
                    distance_miles=stop_result.distance_miles,
                    routes=stop_result.routes,
                    within_radius=stop_result.within_radius,
                    route_departures=self._get_upcoming_departures_for_stop(
                        dataset,
                        stop_result.stop.stop_id,
                        now,
                        limit_per_route=max_departures_per_route,
                        overall_limit=DEFAULT_OVERALL_DEPARTURE_LIMIT,
                        realtime_updates=realtime_updates,
                    ),
                )
            )
        return hydrated

    def _get_upcoming_departures_for_stop(
        self,
        dataset: GTFSData,
        stop_id: str,
        now: datetime,
        *,
        limit_per_route: int,
        overall_limit: int,
        realtime_updates: dict[tuple[str, str], RealtimeStopUpdate],
    ) -> list[RouteDepartureGroup]:
        stop_times = dataset.stop_times_by_stop.get(stop_id, [])
        if not stop_times:
            return []

        candidates: list[DepartureInfo] = []
        earliest_allowed = now - timedelta(seconds=DEPARTURE_GRACE_SECONDS)
        base_service_date = now.date()
        realtime_service_dates = {
            base_service_date - timedelta(days=1),
            base_service_date,
        }

        for day_offset in range(-LOOKBACK_SERVICE_DAYS, LOOKAHEAD_SERVICE_DAYS + 1):
            service_date = base_service_date + timedelta(days=day_offset)
            for stop_time in stop_times:
                trip = dataset.trips.get(stop_time.trip_id)
                if trip is None or not dataset.is_service_active(trip.service_id, service_date):
                    continue

                route = dataset.routes.get(trip.route_id)
                if route is None:
                    continue

                scheduled_departure = dataset.service_date_to_datetime(service_date, stop_time.departure_seconds)
                realtime_update = None
                if service_date in realtime_service_dates:
                    realtime_update = realtime_updates.get((trip.trip_id, stop_id))
                realtime_departure = None
                delay_seconds = None
                source = "scheduled"
                effective_departure = scheduled_departure

                if realtime_update is not None:
                    realtime_departure = realtime_update.departure_time
                    delay_seconds = realtime_update.delay_seconds
                    if realtime_departure is None and delay_seconds is not None:
                        realtime_departure = scheduled_departure + timedelta(seconds=delay_seconds)
                    if realtime_departure is not None:
                        effective_departure = realtime_departure
                        source = "realtime"

                if effective_departure < earliest_allowed:
                    continue

                minutes_until_departure = max(
                    0,
                    math.ceil((effective_departure - now).total_seconds() / 60),
                )
                candidates.append(
                    DepartureInfo(
                        route=route,
                        trip_id=trip.trip_id,
                        headsign=trip.headsign,
                        scheduled_departure=scheduled_departure,
                        realtime_departure=realtime_departure,
                        minutes_until_departure=minutes_until_departure,
                        source=source,
                        delay_seconds=delay_seconds,
                    )
                )

        candidates.sort(
            key=lambda departure: (
                departure.effective_departure,
                departure.route.sort_order,
                departure.route.short_name or departure.route.long_name or departure.route.route_id,
                departure.trip_id,
            )
        )
        if candidates:
            earliest_departure_date = candidates[0].effective_departure.date()
            candidates = [
                departure
                for departure in candidates
                if departure.effective_departure.date() == earliest_departure_date
            ]

        selected: list[DepartureInfo] = []
        route_counts: dict[str, int] = defaultdict(int)
        for departure in candidates:
            route_id = departure.route.route_id
            if route_counts[route_id] >= limit_per_route:
                continue
            selected.append(departure)
            route_counts[route_id] += 1
            if len(selected) >= overall_limit:
                break

        grouped: dict[str, list[DepartureInfo]] = {}
        route_order: list[str] = []
        for departure in selected:
            route_id = departure.route.route_id
            if route_id not in grouped:
                grouped[route_id] = []
                route_order.append(route_id)
            grouped[route_id].append(departure)

        return [
            RouteDepartureGroup(route=grouped_departures[0].route, departures=grouped_departures)
            for route_id in route_order
            for grouped_departures in [grouped[route_id]]
        ]

    def _load_realtime_updates(self, dataset: GTFSData) -> dict[tuple[str, str], RealtimeStopUpdate]:
        if self.realtime_service is None:
            return {}
        return self.realtime_service.get_stop_updates(dataset)

    def _find_direct_candidates(
        self,
        dataset: GTFSData,
        origin_stops: list[NearbyStopResult],
        destination_stops: list[NearbyStopResult],
        shared_route_ids: set[str],
    ) -> list[DirectRouteCandidate]:
        origin_by_stop_id = {result.stop.stop_id: result for result in origin_stops}
        destination_by_stop_id = {result.stop.stop_id: result for result in destination_stops}
        candidates: list[tuple[float, DirectRouteCandidate]] = []

        for route_id in shared_route_ids:
            route = dataset.routes.get(route_id)
            if route is None:
                continue

            best_candidate: tuple[float, DirectRouteCandidate] | None = None
            for trip_id in dataset.route_trip_ids.get(route_id, []):
                trip_sequences = dataset.trip_stop_sequences.get(trip_id, [])
                scanned = self._scan_trip(
                    trip_sequences,
                    origin_by_stop_id,
                    destination_by_stop_id,
                )
                if scanned is None:
                    continue

                origin_result, destination_result, stop_gap = scanned
                trip = dataset.trips[trip_id]
                candidate = DirectRouteCandidate(
                    route=route,
                    origin_stop=origin_result.stop,
                    destination_stop=destination_result.stop,
                    origin_distance_miles=origin_result.distance_miles,
                    destination_distance_miles=destination_result.distance_miles,
                    trip_headsign=trip.headsign,
                    stop_gap=stop_gap,
                )
                score = origin_result.distance_miles + destination_result.distance_miles + (stop_gap * 0.01)
                if best_candidate is None or score < best_candidate[0]:
                    best_candidate = (score, candidate)

            if best_candidate is not None:
                candidates.append(best_candidate)

        candidates.sort(
            key=lambda item: (
                item[0],
                item[1].route.sort_order,
                item[1].route.short_name or item[1].route.route_id,
            )
        )
        return [candidate for _, candidate in candidates]

    @staticmethod
    def _scan_trip(
        trip_sequences: list[tuple[int, str]],
        origin_by_stop_id: dict[str, NearbyStopResult],
        destination_by_stop_id: dict[str, NearbyStopResult],
    ) -> tuple[NearbyStopResult, NearbyStopResult, int] | None:
        best_origin: tuple[int, NearbyStopResult] | None = None

        for sequence, stop_id in trip_sequences:
            if stop_id in origin_by_stop_id:
                candidate_origin = origin_by_stop_id[stop_id]
                if best_origin is None or candidate_origin.distance_miles < best_origin[1].distance_miles:
                    best_origin = (sequence, candidate_origin)
                continue

            if best_origin is not None and stop_id in destination_by_stop_id and sequence > best_origin[0]:
                destination_result = destination_by_stop_id[stop_id]
                return best_origin[1], destination_result, sequence - best_origin[0]

        return None

    @staticmethod
    def _normalize_now(now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(tz=SERVICE_TIMEZONE)
        if now.tzinfo is None:
            return now.replace(tzinfo=SERVICE_TIMEZONE)
        return now.astimezone(SERVICE_TIMEZONE)


__all__ = [
    "CDTAService",
    "DEFAULT_OVERALL_DEPARTURE_LIMIT",
    "DepartureInfo",
    "DirectRouteCandidate",
    "format_checked_at_text",
    "format_departure_clock_time",
    "format_wait_time",
    "GTFSDownloadError",
    "GTFSLoadError",
    "NearbyStopResult",
    "ResolvedLocation",
    "RouteDepartureGroup",
    "TransitLookupResult",
    "find_nearest_stops",
]
