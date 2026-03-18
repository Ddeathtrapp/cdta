from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.config import load_settings


class ConfigTests(unittest.TestCase):
    @patch("utils.config._load_env")
    def test_load_settings_uses_local_defaults_without_storage_root(self, _mock_load_env) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings()

        expected_root = Path(__file__).resolve().parent.parent
        self.assertEqual(settings.project_root, expected_root)
        self.assertIsNone(settings.storage_root)
        self.assertEqual(settings.instance_path, expected_root / "instance")
        self.assertEqual(settings.data_dir, expected_root / "data")
        self.assertEqual(settings.db_path, expected_root / "instance" / "saved_locations.sqlite")
        self.assertEqual(settings.flask_env, "development")
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 5000)
        self.assertTrue(settings.secret_key)
        self.assertIsNone(settings.admin_password)

    @patch("utils.config._load_env")
    def test_load_settings_uses_storage_root_when_configured(self, _mock_load_env) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_STORAGE_DIR": "/data",
                "FLASK_ENV": "production",
                "SECRET_KEY": "prod-secret",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.storage_root, Path("/data"))
        self.assertEqual(settings.instance_path, Path("/data/instance"))
        self.assertEqual(settings.data_dir, Path("/data/data"))
        self.assertEqual(settings.db_path, Path("/data/instance/saved_locations.sqlite"))
        self.assertIsNone(settings.admin_password)
        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 8080)

    @patch("utils.config._load_env")
    def test_load_settings_requires_secret_key_in_production(self, _mock_load_env) -> None:
        with patch.dict(
            os.environ,
            {
                "FLY_APP_NAME": "cdta-test-app",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY must be set"):
                load_settings()

    @patch("utils.config._load_env")
    def test_load_settings_defaults_to_production_on_fly(self, _mock_load_env) -> None:
        with patch.dict(
            os.environ,
            {
                "FLY_APP_NAME": "cdta-test-app",
                "SECRET_KEY": "prod-secret",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.flask_env, "production")
        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 8080)

    @patch("utils.config._load_env")
    def test_load_settings_reads_admin_password_when_configured(self, _mock_load_env) -> None:
        with patch.dict(
            os.environ,
            {
                "SECRET_KEY": "dev-secret",
                "ADMIN_PASSWORD": "admin-secret",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.admin_password, "admin-secret")


if __name__ == "__main__":
    unittest.main()
