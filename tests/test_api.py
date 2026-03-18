from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from app import create_app
from services.geocode import GeocodeError, GeocodeMatch
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

    def test_saved_locations_api_requires_admin(self) -> None:
        response = self.client.get("/api/saved-locations")

        self.assertEqual(response.status_code, 401)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "admin_required")

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
        self.assertTrue(payload["destination_serviceable"])
        self.assertIn("checked_at_display", payload)
        self.assertEqual(
            [departure["minutes"] for departure in route_payload["next_departures"]],
            [5, 29],
        )
        self.assertEqual(
            route_payload["next_departures"][0]["display_wait"],
            "in 5 min",
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

    def test_reverse_geocode_api_returns_label(self) -> None:
        geocoder = self.app.extensions["geocoder"]
        geocoder.reverse_geocode = Mock(
            return_value=GeocodeMatch(
                label="Empire State Plaza, Albany, NY 12223",
                latitude=42.6526,
                longitude=-73.7562,
            )
        )

        response = self.client.get(
            "/api/reverse-geocode",
            query_string={
                "latitude": "42.6526",
                "longitude": "-73.7562",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["label"], "Empire State Plaza, Albany, NY 12223")
        self.assertEqual(payload["provider"], "nominatim")
        geocoder.reverse_geocode.assert_called_once_with(42.6526, -73.7562)

    def test_reverse_geocode_api_returns_404_when_address_is_missing(self) -> None:
        geocoder = self.app.extensions["geocoder"]
        geocoder.reverse_geocode = Mock(
            side_effect=GeocodeError("No nearby address was found for those coordinates.")
        )

        response = self.client.get(
            "/api/reverse-geocode",
            query_string={
                "latitude": "42.6526",
                "longitude": "-73.7562",
            },
        )

        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "reverse_geocode_failed")

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

    def test_lookup_api_marks_far_destination_as_not_serviceable(self) -> None:
        response = self.client.get(
            "/api/lookup",
            query_string={
                "origin_lat": "42.6526",
                "origin_lon": "-73.7562",
                "destination_lat": "43.0000",
                "destination_lon": "-74.5000",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["destination_serviceable"])
        self.assertEqual(
            payload["destination_service_message"],
            "Destination appears outside the CDTA service area.",
        )


if __name__ == "__main__":
    unittest.main()
