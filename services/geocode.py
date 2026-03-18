from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from utils.validation import ValidationError, parse_coordinate_fields, require_text


class GeocodeError(ValidationError):
    """Raised when an address cannot be geocoded."""


@dataclass(frozen=True)
class GeocodeMatch:
    label: str
    latitude: float
    longitude: float


class LocationGeocoder:
    geocode_endpoint = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    reverse_geocode_endpoint = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self, *, timeout_seconds: float, user_agent: str) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "en",
            }
        )

    def geocode(self, raw_address: str) -> GeocodeMatch:
        address = require_text(raw_address, "Address", max_length=200)

        try:
            response = self.session.get(
                self.geocode_endpoint,
                params={
                    "address": address,
                    "benchmark": "Public_AR_Current",
                    "format": "json",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise GeocodeError("Could not reach the U.S. Census Geocoder right now.") from exc

        payload = response.json()
        matches = payload.get("result", {}).get("addressMatches", [])
        if not matches:
            raise GeocodeError("No matching U.S. address was found for that input.")

        first_match = matches[0]
        coordinates = first_match.get("coordinates", {})
        label = first_match.get("matchedAddress") or address
        longitude = float(coordinates["x"])
        latitude = float(coordinates["y"])
        return GeocodeMatch(label=label, latitude=latitude, longitude=longitude)

    def reverse_geocode(self, latitude: Any, longitude: Any) -> GeocodeMatch:
        resolved_latitude, resolved_longitude = parse_coordinate_fields(
            latitude,
            longitude,
            field_name="Reverse geocode",
        )

        try:
            response = self.session.get(
                self.reverse_geocode_endpoint,
                params={
                    "lat": resolved_latitude,
                    "lon": resolved_longitude,
                    "format": "jsonv2",
                    "zoom": 18,
                    "addressdetails": 1,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise GeocodeError("Could not reach OpenStreetMap Nominatim right now.") from exc

        payload = response.json()
        label = str(payload.get("display_name") or "").strip()
        if not label:
            raise GeocodeError("No nearby address was found for those coordinates.")

        return GeocodeMatch(
            label=label,
            latitude=resolved_latitude,
            longitude=resolved_longitude,
        )


CensusGeocoder = LocationGeocoder
