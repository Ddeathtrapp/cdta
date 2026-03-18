from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app import create_app
from services.gtfs_loader import SERVICE_TIMEZONE
from tests.helpers import create_sample_gtfs_feed, make_test_settings


class HtmlResultsTests(unittest.TestCase):
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

    def test_search_post_redirects_to_results_get(self) -> None:
        response = self.client.post(
            "/search",
            data={
                "origin_mode": "current",
                "origin_current_lat": "42.6526",
                "origin_current_lon": "-73.7562",
                "origin_current_label": "Browser location 42.652600, -73.756200",
                "destination_mode": "saved",
                "destination_saved_id": str(self.saved_location.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertIn("/results?", location)
        self.assertIn("origin_mode=current", location)
        self.assertIn("destination_mode=saved", location)

    def test_results_page_shows_checked_at_and_human_friendly_departures(self) -> None:
        response = self.client.get(
            "/results",
            query_string={
                "origin_mode": "current",
                "origin_current_lat": "42.6526",
                "origin_current_lon": "-73.7562",
                "origin_current_label": "Browser location 42.652600, -73.756200",
                "destination_mode": "saved",
                "destination_saved_id": str(self.saved_location.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Checked at 8:00 AM on March 19, 2026", html)
        self.assertIn("Routes", html)
        self.assertNotIn("Destination side", html)
        self.assertIn("Refresh Times", html)
        self.assertIn("/results?origin_mode=current", html)
        self.assertIn("in 5 min (departure time: 8:05 AM)", html)

    def test_results_get_can_be_refreshed_without_post_body(self) -> None:
        query_string = {
            "origin_mode": "current",
            "origin_current_lat": "42.6526",
            "origin_current_lon": "-73.7562",
            "origin_current_label": "Browser location 42.652600, -73.756200",
            "destination_mode": "saved",
            "destination_saved_id": str(self.saved_location.id),
        }

        first = self.client.get("/results", query_string=query_string)
        second = self.client.get("/results", query_string=query_string)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)


if __name__ == "__main__":
    unittest.main()
