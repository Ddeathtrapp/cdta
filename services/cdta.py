from __future__ import annotations

from dataclasses import dataclass

from services.gtfs_loader import GTFSData, GTFSDownloadError, GTFSLoadError, GTFSLoader, RouteInfo, StopInfo
from utils.geo import format_coordinates, haversine_miles


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
class NearbyStopResult:
    stop: StopInfo
    distance_miles: float
    routes: list[RouteInfo]
    within_radius: bool


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
    ) -> None:
        self.loader = loader
        self.nearby_stop_limit = nearby_stop_limit
        self.nearby_stop_radius_miles = nearby_stop_radius_miles

    def get_feed_status(self) -> dict[str, str | bool | None]:
        return self.loader.get_feed_status()

    def refresh_feed(self) -> str:
        return str(self.loader.refresh_feed())

    def lookup(self, origin: ResolvedLocation, destination: ResolvedLocation) -> TransitLookupResult:
        dataset = self.loader.load()
        origin_stops = find_nearest_stops(
            dataset,
            origin.latitude,
            origin.longitude,
            limit=self.nearby_stop_limit,
            radius_miles=self.nearby_stop_radius_miles,
        )
        destination_stops = find_nearest_stops(
            dataset,
            destination.latitude,
            destination.longitude,
            limit=self.nearby_stop_limit,
            radius_miles=self.nearby_stop_radius_miles,
        )

        origin_route_ids = {route.route_id for stop in origin_stops for route in stop.routes}
        destination_route_ids = {route.route_id for stop in destination_stops for route in stop.routes}
        shared_route_ids = origin_route_ids & destination_route_ids

        return TransitLookupResult(
            origin_stops=origin_stops,
            destination_stops=destination_stops,
            shared_routes=sort_routes(shared_route_ids, dataset.routes),
            direct_candidates=self._find_direct_candidates(dataset, origin_stops, destination_stops, shared_route_ids),
            note=(
                "Nearby stops and shared routes are a helpful shortlist, but this MVP does not guarantee a full "
                "door-to-door itinerary. Direct-route candidates are heuristic matches based on GTFS stop order."
            ),
        )

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


__all__ = [
    "CDTAService",
    "DirectRouteCandidate",
    "GTFSDownloadError",
    "GTFSLoadError",
    "NearbyStopResult",
    "ResolvedLocation",
    "TransitLookupResult",
    "find_nearest_stops",
]
