from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from app import create_app
from services.gtfs_loader import SERVICE_TIMEZONE
from tests.helpers import create_sample_gtfs_feed, make_test_settings


class AdminAuthTests(unittest.TestCase):
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

    def _get_csrf_token(self) -> str:
        with self.client.session_transaction() as active_session:
            return active_session["_csrf_token"]

    def _login(self, password: str = "test-admin-password", next_url: str = "/saved-locations"):
        self.client.get("/admin/login", query_string={"next": next_url})
        csrf_token = self._get_csrf_token()
        return self.client.post(
            "/admin/login",
            data={
                "password": password,
                "next": next_url,
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

    def test_public_routes_remain_available_anonymously(self) -> None:
        home = self.client.get("/")
        results = self.client.get(
            "/results",
            query_string={
                "origin_mode": "current",
                "origin_current_lat": "42.6526",
                "origin_current_lon": "-73.7562",
                "origin_current_label": "Empire State Plaza, Albany, NY",
                "destination_mode": "current",
                "destination_current_lat": "42.6540",
                "destination_current_lon": "-73.7500",
                "destination_current_label": "Broadway & State St, Albany, NY",
            },
        )
        health = self.client.get("/api/health")
        lookup = self.client.get(
            "/api/lookup",
            query_string={
                "origin_lat": "42.6526",
                "origin_lon": "-73.7562",
                "destination_lat": "42.6540",
                "destination_lon": "-73.7500",
            },
        )

        self.assertEqual(home.status_code, 200)
        self.assertEqual(results.status_code, 200)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(lookup.status_code, 200)

    def test_home_page_hides_admin_controls_for_anonymous_users(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Admin Login", html)
        self.assertNotIn("Refresh GTFS", html)
        self.assertNotIn("Saved Locations", html)
        self.assertNotIn("Use a saved nickname", html)

    def test_saved_locations_page_redirects_to_login_when_anonymous(self) -> None:
        response = self.client.get("/saved-locations", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login?next=/saved-locations", response.headers["Location"])

    def test_protected_post_route_rejects_anonymous_user(self) -> None:
        response = self.client.post(
            "/saved-locations/create",
            data={
                "nickname": "Work",
                "location_input": "42.6526,-73.7562",
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_login_success_with_correct_password(self) -> None:
        response = self._login()

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/saved-locations"))
        with self.client.session_transaction() as active_session:
            self.assertTrue(active_session.get("is_admin"))

    def test_login_failure_with_wrong_password(self) -> None:
        response = self._login(password="wrong-password")

        self.assertEqual(response.status_code, 401)
        self.assertIn("Incorrect admin password.", response.get_data(as_text=True))
        with self.client.session_transaction() as active_session:
            self.assertFalse(active_session.get("is_admin", False))

    def test_logout_clears_admin_session(self) -> None:
        self._login()
        csrf_token = self._get_csrf_token()

        response = self.client.post(
            "/admin/logout",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))
        with self.client.session_transaction() as active_session:
            self.assertNotIn("is_admin", active_session)

    def test_csrf_token_is_required_for_admin_post_actions(self) -> None:
        self._login()
        store = self.app.extensions["location_store"]

        response = self.client.post(
            "/saved-locations/create",
            data={
                "nickname": "Work",
                "location_input": "42.6526,-73.7562",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(store.list_locations()), 1)

    def test_refresh_gtfs_requires_admin(self) -> None:
        response = self.client.post("/refresh-gtfs")

        self.assertEqual(response.status_code, 401)

    def test_refresh_gtfs_allows_admin_with_csrf(self) -> None:
        self._login()
        csrf_token = self._get_csrf_token()
        service = self.app.extensions["cdta_service"]
        service.refresh_feed = Mock(return_value=Path("C:/tmp/google_transit.zip"))

        response = self.client.post(
            "/refresh-gtfs",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        service.refresh_feed.assert_called_once_with()

    def test_api_saved_locations_returns_records_for_admin(self) -> None:
        self._login()

        response = self.client.get("/api/saved-locations")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["saved_locations"][0]["nickname"], "Home")

    def test_admin_login_is_disabled_without_configured_password(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        project_root = Path(temp_dir.name)
        data_dir = project_root / "data"
        instance_dir = project_root / "instance"
        data_dir.mkdir(parents=True, exist_ok=True)
        instance_dir.mkdir(parents=True, exist_ok=True)
        create_sample_gtfs_feed(data_dir / "google_transit.zip")

        settings = replace(make_test_settings(project_root), admin_password=None)
        app = create_app(settings)
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/admin/login")

        self.assertEqual(response.status_code, 503)
        self.assertIn("Admin login is disabled until ADMIN_PASSWORD is configured.", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
