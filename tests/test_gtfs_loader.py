from __future__ import annotations

import unittest

from services.gtfs_loader import parse_gtfs_time_to_seconds


class GTFSTimeParsingTests(unittest.TestCase):
    def test_parses_standard_time(self) -> None:
        self.assertEqual(parse_gtfs_time_to_seconds("08:05:00"), 8 * 3600 + 5 * 60)

    def test_parses_time_past_24_hours(self) -> None:
        self.assertEqual(parse_gtfs_time_to_seconds("25:05:00"), 25 * 3600 + 5 * 60)


if __name__ == "__main__":
    unittest.main()
