from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from services.gtfs_loader import GTFSData, SERVICE_TIMEZONE


LOGGER = logging.getLogger(__name__)

try:
    from google.transit import gtfs_realtime_pb2
except ImportError:  # pragma: no cover - handled gracefully at runtime
    gtfs_realtime_pb2 = None


@dataclass(frozen=True)
class RealtimeStopUpdate:
    trip_id: str
    stop_id: str
    departure_time: datetime | None
    delay_seconds: int | None


class GTFSRealtimeService:
    def __init__(
        self,
        *,
        enabled: bool,
        trip_updates_url: str | None,
        api_key: str | None,
        timeout_seconds: float,
        user_agent: str,
        cache_ttl_seconds: float = 20.0,
    ) -> None:
        self.enabled = enabled
        self.trip_updates_url = trip_updates_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._cached_updates: dict[tuple[str, str], RealtimeStopUpdate] = {}
        self._cache_expires_at = 0.0

    def get_stop_updates(self, dataset: GTFSData) -> dict[tuple[str, str], RealtimeStopUpdate]:
        if not self.enabled or not self.trip_updates_url:
            return {}

        if gtfs_realtime_pb2 is None:
            LOGGER.warning("Realtime feed requested but gtfs-realtime-bindings is not installed.")
            return {}

        now_monotonic = time.monotonic()
        if now_monotonic < self._cache_expires_at:
            return self._cached_updates

        headers: dict[str, str] = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        try:
            response = self.session.get(
                self.trip_updates_url,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
        except requests.RequestException as exc:
            LOGGER.warning("Could not fetch CDTA realtime trip updates: %s", exc)
            self._cache_updates({}, now_monotonic)
            return {}
        except Exception as exc:  # pragma: no cover - protobuf parsing failure is environment-dependent
            LOGGER.warning("Could not parse CDTA realtime trip updates: %s", exc)
            self._cache_updates({}, now_monotonic)
            return {}

        updates: dict[tuple[str, str], RealtimeStopUpdate] = {}
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue

            trip_update = entity.trip_update
            trip_id = trip_update.trip.trip_id.strip()
            if not trip_id:
                continue

            stop_id_by_sequence = dataset.trip_stop_ids_by_sequence.get(trip_id, {})
            for stop_time_update in trip_update.stop_time_update:
                stop_id = stop_time_update.stop_id.strip() if stop_time_update.stop_id else ""
                if not stop_id and stop_time_update.stop_sequence:
                    stop_id = stop_id_by_sequence.get(int(stop_time_update.stop_sequence), "")
                if not stop_id:
                    continue

                departure_time = None
                delay_seconds = None

                if stop_time_update.HasField("departure"):
                    if stop_time_update.departure.time:
                        departure_time = datetime.fromtimestamp(
                            stop_time_update.departure.time,
                            tz=timezone.utc,
                        ).astimezone(SERVICE_TIMEZONE)
                    if stop_time_update.departure.HasField("delay"):
                        delay_seconds = int(stop_time_update.departure.delay)

                if departure_time is None and stop_time_update.HasField("arrival") and stop_time_update.arrival.time:
                    departure_time = datetime.fromtimestamp(
                        stop_time_update.arrival.time,
                        tz=timezone.utc,
                    ).astimezone(SERVICE_TIMEZONE)

                if delay_seconds is None and stop_time_update.HasField("arrival") and stop_time_update.arrival.HasField("delay"):
                    delay_seconds = int(stop_time_update.arrival.delay)

                if departure_time is None and delay_seconds is None:
                    continue

                updates[(trip_id, stop_id)] = RealtimeStopUpdate(
                    trip_id=trip_id,
                    stop_id=stop_id,
                    departure_time=departure_time,
                    delay_seconds=delay_seconds,
                )

        self._cache_updates(updates, now_monotonic)
        return updates

    def _cache_updates(self, updates: dict[tuple[str, str], RealtimeStopUpdate], now_monotonic: float) -> None:
        self._cached_updates = updates
        self._cache_expires_at = now_monotonic + self.cache_ttl_seconds
