from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app import create_app
from services.gtfs_loader import SERVICE_TIMEZONE
from tests.helpers import create_sample_gtfs_feed, make_test_settings


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.project_root = Path(self.temp_dir.name)
        data_dir = self.project_root / "data"
        instance_dir = self.project_root / "instance"
        data_dir.mkdir(parents=True, exist_ok=True)
        instance_dir.mkdir(parents=True, exist_ok=True)
        create_sample_gtfs_feed(data_dir / "google_transit.zip")

        settings = make_test_settings(self.project_root)
        self.app = create_app(settings)
        self.app.config["TESTING"] = True
        self.app.config["NOW_PROVIDER"] = lambda: datetime(2026, 3, 19, 8, 0, tzinfo=SERVICE_TIMEZONE)
        self.client = self.app.test_client()

        store = self.app.extensions["location_store"]
        self.saved_location = store.create_location("Home", "Broadway & State St", 42.6540, -73.7500)

    def test_saved_locations_api_returns_records(self) -> None:
        response = self.client.get("/api/saved-locations")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["saved_locations"][0]["nickname"], "Home")

    def test_lookup_api_get_happy_path(self) -> None:
        response = self.client.get(
            "/api/lookup",
            query_string={
                "origin_lat": "42.6526",
                "origin_lon": "-73.7562",
                "destination_saved": "Home",
                "max_stops": "1",
                "max_departures_per_route": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["realtime_used"])
        self.assertEqual(payload["origin_nearest_stops"][0]["stop_name"], "Madison Ave & Swan St")
        route_payload = payload["origin_nearest_stops"][0]["routes"][0]
        self.assertEqual(route_payload["display_name"], "910 - BusPlus Red Line")
        self.assertEqual(
            [departure["minutes"] for departure in route_payload["next_departures"]],
            [5, 29],
        )

    def test_lookup_api_post_happy_path(self) -> None:
        response = self.client.post(
            "/api/lookup",
            json={
                "origin": {
                    "mode": "current",
                    "latitude": 42.6526,
                    "longitude": -73.7562,
                },
                "destination": {
                    "mode": "saved",
                    "saved_id": self.saved_location.id,
                },
                "max_stops": 1,
                "max_departures_per_route": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["destination"]["saved_location_id"], self.saved_location.id)
        self.assertEqual(payload["shared_routes"][0]["display_name"], "910 - BusPlus Red Line")

    def test_lookup_api_returns_json_validation_error(self) -> None:
        response = self.client.get("/api/lookup", query_string={"destination_saved": "Home"})

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_lookup_api_returns_404_for_missing_saved_location(self) -> None:
        response = self.client.get(
            "/api/lookup",
            query_string={
                "origin_lat": "42.6526",
                "origin_lon": "-73.7562",
                "destination_saved": "Missing Place",
            },
        )

        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
