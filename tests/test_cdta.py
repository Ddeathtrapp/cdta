from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from services.cdta import CDTAService, find_nearest_stops
from services.gtfs_loader import GTFSLoader, SERVICE_TIMEZONE
from tests.helpers import create_sample_gtfs_feed


class NearestStopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        project_root = Path(self.temp_dir.name)
        data_dir = project_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        create_sample_gtfs_feed(data_dir / "google_transit.zip")

        self.loader = GTFSLoader(
            feed_url="https://example.invalid/google_transit.zip",
            data_dir=data_dir,
            timeout_seconds=5.0,
            user_agent="cdta-local-lookup-tests/1.0",
        )
        self.dataset = self.loader.load()
        self.service = CDTAService(
            self.loader,
            nearby_stop_limit=5,
            nearby_stop_radius_miles=0.75,
        )

    def test_nearest_stop_is_listed_first(self) -> None:
        results = find_nearest_stops(
            self.dataset,
            42.6527,
            -73.7563,
            limit=2,
            radius_miles=0.75,
        )

        self.assertEqual(results[0].stop.stop_id, "STOP1")
        self.assertEqual(results[0].routes[0].display_name, "910 - BusPlus Red Line")

    def test_falls_back_to_nearest_stop_when_radius_is_tight(self) -> None:
        results = find_nearest_stops(
            self.dataset,
            42.7000,
            -73.8000,
            limit=1,
            radius_miles=0.01,
        )

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].within_radius)

    def test_service_calendar_respects_exceptions(self) -> None:
        self.assertFalse(self.dataset.is_service_active("WEEKDAY", date(2026, 3, 18)))
        self.assertTrue(self.dataset.is_service_active("WEEKDAY", date(2026, 3, 19)))
        self.assertTrue(self.dataset.is_service_active("SPECIAL_EVENT", date(2026, 3, 18)))

    def test_upcoming_departures_group_by_route(self) -> None:
        now = datetime(2026, 3, 19, 8, 0, tzinfo=SERVICE_TIMEZONE)

        departures = self.service.get_upcoming_departures_for_stop("STOP1", now=now)

        self.assertEqual([group.route.short_name for group in departures], ["910", "114"])
        self.assertEqual(
            [departure.minutes_until_departure for departure in departures[0].departures],
            [5, 29],
        )
        self.assertEqual(departures[0].headsign, "Downtown Albany")
        self.assertEqual(
            [departure.minutes_until_departure for departure in departures[1].departures],
            [11],
        )

    def test_upcoming_departures_handles_gtfs_times_past_midnight(self) -> None:
        now = datetime(2026, 3, 20, 0, 10, tzinfo=SERVICE_TIMEZONE)

        departures = self.service.get_upcoming_departures_for_stop("STOP1", now=now)

        overnight_group = departures[0]
        self.assertEqual(overnight_group.route.short_name, "910")
        self.assertEqual(overnight_group.departures[0].minutes_until_departure, 55)
        self.assertEqual(
            overnight_group.departures[0].scheduled_departure.isoformat(),
            "2026-03-20T01:05:00-04:00",
        )

    def test_upcoming_departures_return_empty_when_no_future_service(self) -> None:
        now = datetime(2031, 1, 1, 12, 0, tzinfo=SERVICE_TIMEZONE)

        departures = self.service.get_upcoming_departures_for_stop("STOP1", now=now)

        self.assertEqual(departures, [])


if __name__ == "__main__":
    unittest.main()
