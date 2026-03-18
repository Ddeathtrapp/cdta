from __future__ import annotations

from math import asin, cos, radians, sin, sqrt


EARTH_RADIUS_MILES = 3958.7613


def haversine_miles(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    lat1 = radians(latitude_a)
    lon1 = radians(longitude_a)
    lat2 = radians(latitude_b)
    lon2 = radians(longitude_b)

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    haversine = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * asin(sqrt(haversine))


def format_coordinates(latitude: float, longitude: float) -> str:
    return f"{latitude:.5f}, {longitude:.5f}"
