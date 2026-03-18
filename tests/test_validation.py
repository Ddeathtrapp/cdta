from __future__ import annotations

import unittest

from utils.validation import ValidationError, parse_lat_lon_input, validate_coordinate_pair


class LatLonValidationTests(unittest.TestCase):
    def test_parse_valid_lat_lon_pair(self) -> None:
        self.assertEqual(parse_lat_lon_input("42.6526,-73.7562"), (42.6526, -73.7562))

    def test_address_text_is_not_treated_as_coordinate_pair(self) -> None:
        self.assertIsNone(parse_lat_lon_input("110 State St, Albany, NY"))

    def test_out_of_range_coordinates_raise_validation_error(self) -> None:
        with self.assertRaises(ValidationError):
            validate_coordinate_pair(91.0, -73.75)


if __name__ == "__main__":
    unittest.main()
