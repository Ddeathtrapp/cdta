from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def _load_env(path: Path) -> None:
    for key, value in _read_env_file(path).items():
        os.environ.setdefault(key, value)


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw)


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    project_root: Path
    storage_root: Path | None
    instance_path: Path
    data_dir: Path
    db_path: Path
    flask_env: str
    secret_key: str
    host: str
    port: int
    debug: bool
    cdta_gtfs_url: str
    http_timeout_seconds: float
    http_user_agent: str
    nearby_stop_limit: int
    nearby_stop_radius_miles: float
    enable_realtime: bool
    realtime_trip_updates_url: str | None
    realtime_api_key: str | None
    realtime_vehicle_positions_url: str | None


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parent.parent
    _load_env(project_root / ".env")

    is_fly_runtime = bool(os.environ.get("FLY_APP_NAME"))
    flask_env = os.environ.get("FLASK_ENV", "production" if is_fly_runtime else "development").strip().lower()
    storage_root_text = os.environ.get("APP_STORAGE_DIR", "").strip()
    storage_root = Path(storage_root_text) if storage_root_text else None

    if storage_root is not None:
        instance_path = storage_root / "instance"
        data_dir = storage_root / "data"
    else:
        instance_path = project_root / "instance"
        data_dir = project_root / "data"

    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        if flask_env == "production":
            raise RuntimeError("SECRET_KEY must be set when FLASK_ENV=production.")
        secret_key = secrets.token_hex(32)

    return Settings(
        project_root=project_root,
        storage_root=storage_root,
        instance_path=instance_path,
        data_dir=data_dir,
        db_path=instance_path / "saved_locations.sqlite",
        flask_env=flask_env,
        secret_key=secret_key,
        host=os.environ.get("HOST", "0.0.0.0" if flask_env == "production" else "127.0.0.1"),
        port=_get_int("PORT", 8080 if flask_env == "production" else 5000),
        debug=_get_bool("FLASK_DEBUG", flask_env == "development"),
        cdta_gtfs_url=os.environ.get(
            "CDTA_GTFS_URL",
            "https://www.cdta.org/schedules/google_transit.zip",
        ),
        http_timeout_seconds=_get_float("HTTP_TIMEOUT_SECONDS", 12.0),
        http_user_agent=os.environ.get(
            "HTTP_USER_AGENT",
            "cdta-local-lookup/1.0 (+local app)",
        ),
        nearby_stop_limit=_get_int("NEARBY_STOP_LIMIT", 5),
        nearby_stop_radius_miles=_get_float("NEARBY_STOP_RADIUS_MILES", 0.75),
        enable_realtime=_get_bool("ENABLE_REALTIME", False),
        realtime_trip_updates_url=os.environ.get("CDTA_REALTIME_TRIP_UPDATES_URL") or None,
        realtime_api_key=os.environ.get("CDTA_REALTIME_API_KEY") or None,
        realtime_vehicle_positions_url=os.environ.get("CDTA_REALTIME_VEHICLE_POSITIONS_URL") or None,
    )
