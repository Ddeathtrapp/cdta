from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from services.cdta import find_nearest_stops
from services.gtfs_loader import GTFSData, RouteInfo, StopInfo


class NearestStopTests(unittest.TestCase):
    def setUp(self) -> None:
        route_10 = RouteInfo(
            route_id="10",
            short_name="10",
            long_name="Western Avenue",
            description="",
            url="",
            color="123573",
            text_color="FFFFFF",
            sort_order=10,
        )
        route_1 = RouteInfo(
            route_id="1",
            short_name="1",
            long_name="Central Avenue",
            description="",
            url="",
            color="123573",
            text_color="FFFFFF",
            sort_order=1,
        )
        stop_a = StopInfo(
            stop_id="A",
            code="A",
            name="Closest Stop",
            latitude=42.6526,
            longitude=-73.7562,
            url="",
        )
        stop_b = StopInfo(
            stop_id="B",
            code="B",
            name="Second Stop",
            latitude=42.6600,
            longitude=-73.7600,
            url="",
        )

        self.dataset = GTFSData(
            feed_path=Path("test-feed.zip"),
            loaded_at=datetime.now(),
            routes={
                route_10.route_id: route_10,
                route_1.route_id: route_1,
            },
            stops={
                stop_a.stop_id: stop_a,
                stop_b.stop_id: stop_b,
            },
            trips={},
            stop_routes={
                "A": {"10"},
                "B": {"1"},
            },
            route_trip_ids={},
            trip_stop_sequences={},
        )

    def test_nearest_stop_is_listed_first(self) -> None:
        results = find_nearest_stops(
            self.dataset,
            42.6527,
            -73.7563,
            limit=2,
            radius_miles=0.75,
        )

        self.assertEqual(results[0].stop.stop_id, "A")
        self.assertEqual(results[0].routes[0].route_id, "10")

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


if __name__ == "__main__":
    unittest.main()
