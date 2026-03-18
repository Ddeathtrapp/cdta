from __future__ import annotations

from typing import Any


class ValidationError(ValueError):
    """Raised when user input fails validation."""


def require_text(value: Any, field_name: str, *, max_length: int = 255) -> str:
    if value is None:
        raise ValidationError(f"{field_name} is required.")

    cleaned = str(value).strip()
    if not cleaned:
        raise ValidationError(f"{field_name} is required.")
    if len(cleaned) > max_length:
        raise ValidationError(f"{field_name} must be {max_length} characters or fewer.")
    return cleaned


def normalize_nickname(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def validate_nickname(value: Any) -> str:
    nickname = require_text(value, "Nickname", max_length=80)
    normalized = normalize_nickname(nickname)
    if not normalized:
        raise ValidationError("Nickname is required.")
    return nickname


def validate_coordinate_pair(latitude: float, longitude: float) -> tuple[float, float]:
    if not (-90 <= latitude <= 90):
        raise ValidationError("Latitude must be between -90 and 90.")
    if not (-180 <= longitude <= 180):
        raise ValidationError("Longitude must be between -180 and 180.")
    return latitude, longitude


def parse_lat_lon_input(raw_value: Any) -> tuple[float, float] | None:
    if raw_value is None:
        return None

    text = str(raw_value).strip()
    if not text:
        return None

    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 2:
        return None

    try:
        latitude = float(parts[0])
        longitude = float(parts[1])
    except ValueError:
        return None

    return validate_coordinate_pair(latitude, longitude)


def parse_coordinate_fields(latitude: Any, longitude: Any, *, field_name: str) -> tuple[float, float]:
    lat_text = require_text(latitude, f"{field_name} latitude", max_length=40)
    lon_text = require_text(longitude, f"{field_name} longitude", max_length=40)

    try:
        lat_value = float(lat_text)
        lon_value = float(lon_text)
    except ValueError as exc:
        raise ValidationError(f"{field_name} coordinates must be numeric.") from exc

    return validate_coordinate_pair(lat_value, lon_value)
