from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.locations import DuplicateNicknameError, LocationStore


class SavedLocationStoreTests(unittest.TestCase):
    def test_nickname_uniqueness_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "saved_locations.sqlite"
            store = LocationStore(db_path)
            store.initialize()

            store.create_location("Home", "110 State St Albany NY", 42.6503, -73.7540)

            with self.assertRaises(DuplicateNicknameError):
                store.create_location("home", "1 Crossgates Mall Rd Albany NY", 42.6881, -73.8498)


if __name__ == "__main__":
    unittest.main()
