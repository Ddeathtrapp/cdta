from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from utils.config import Settings


def write_gtfs_zip(
    path: Path,
    *,
    routes: list[dict[str, str]],
    stops: list[dict[str, str]],
    trips: list[dict[str, str]],
    stop_times: list[dict[str, str]],
    calendars: list[dict[str, str]],
    calendar_dates: list[dict[str, str]],
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_csv(archive, "routes.txt", routes)
        _write_csv(archive, "stops.txt", stops)
        _write_csv(archive, "trips.txt", trips)
        _write_csv(archive, "stop_times.txt", stop_times)
        _write_csv(archive, "calendar.txt", calendars)
        _write_csv(archive, "calendar_dates.txt", calendar_dates)


def create_sample_gtfs_feed(path: Path) -> None:
    write_gtfs_zip(
        path,
        routes=[
            {
                "route_id": "R1",
                "agency_id": "CDTA",
                "route_short_name": "910",
                "route_long_name": "BusPlus Red Line",
                "route_desc": "",
                "route_type": "3",
                "route_url": "",
                "route_color": "123573",
                "route_text_color": "FFFFFF",
                "route_sort_order": "1",
            },
            {
                "route_id": "R2",
                "agency_id": "CDTA",
                "route_short_name": "114",
                "route_long_name": "Madison / Washington",
                "route_desc": "",
                "route_type": "3",
                "route_url": "",
                "route_color": "6A1B1A",
                "route_text_color": "FFFFFF",
                "route_sort_order": "2",
            },
        ],
        stops=[
            {
                "stop_id": "STOP1",
                "stop_code": "STOP1",
                "stop_name": "Madison Ave & Swan St",
                "stop_lat": "42.6526",
                "stop_lon": "-73.7562",
                "location_type": "0",
                "stop_url": "",
            },
            {
                "stop_id": "STOP2",
                "stop_code": "STOP2",
                "stop_name": "Broadway & State St",
                "stop_lat": "42.6540",
                "stop_lon": "-73.7500",
                "location_type": "0",
                "stop_url": "",
            },
            {
                "stop_id": "STOP3",
                "stop_code": "STOP3",
                "stop_name": "Washington Ave & Lark St",
                "stop_lat": "42.6560",
                "stop_lon": "-73.7650",
                "location_type": "0",
                "stop_url": "",
            },
        ],
        trips=[
            {
                "route_id": "R1",
                "service_id": "WEEKDAY",
                "trip_id": "TRIP_R1_0805",
                "trip_headsign": "Downtown Albany",
                "direction_id": "0",
            },
            {
                "route_id": "R1",
                "service_id": "WEEKDAY",
                "trip_id": "TRIP_R1_0829",
                "trip_headsign": "Downtown Albany",
                "direction_id": "0",
            },
            {
                "route_id": "R2",
                "service_id": "WEEKDAY",
                "trip_id": "TRIP_R2_0811",
                "trip_headsign": "Crossgates Mall",
                "direction_id": "1",
            },
            {
                "route_id": "R2",
                "service_id": "SPECIAL_EVENT",
                "trip_id": "TRIP_SPECIAL_0900",
                "trip_headsign": "Special Shuttle",
                "direction_id": "0",
            },
            {
                "route_id": "R1",
                "service_id": "WEEKDAY",
                "trip_id": "TRIP_OVERNIGHT_2505",
                "trip_headsign": "Late Night Albany",
                "direction_id": "0",
            },
        ],
        stop_times=[
            {
                "trip_id": "TRIP_R1_0805",
                "arrival_time": "08:05:00",
                "departure_time": "08:05:00",
                "stop_id": "STOP1",
                "stop_sequence": "1",
            },
            {
                "trip_id": "TRIP_R1_0805",
                "arrival_time": "08:20:00",
                "departure_time": "08:20:00",
                "stop_id": "STOP2",
                "stop_sequence": "2",
            },
            {
                "trip_id": "TRIP_R1_0829",
                "arrival_time": "08:29:00",
                "departure_time": "08:29:00",
                "stop_id": "STOP1",
                "stop_sequence": "1",
            },
            {
                "trip_id": "TRIP_R1_0829",
                "arrival_time": "08:44:00",
                "departure_time": "08:44:00",
                "stop_id": "STOP2",
                "stop_sequence": "2",
            },
            {
                "trip_id": "TRIP_R2_0811",
                "arrival_time": "08:11:00",
                "departure_time": "08:11:00",
                "stop_id": "STOP1",
                "stop_sequence": "1",
            },
            {
                "trip_id": "TRIP_R2_0811",
                "arrival_time": "08:25:00",
                "departure_time": "08:25:00",
                "stop_id": "STOP3",
                "stop_sequence": "2",
            },
            {
                "trip_id": "TRIP_SPECIAL_0900",
                "arrival_time": "09:00:00",
                "departure_time": "09:00:00",
                "stop_id": "STOP1",
                "stop_sequence": "1",
            },
            {
                "trip_id": "TRIP_SPECIAL_0900",
                "arrival_time": "09:12:00",
                "departure_time": "09:12:00",
                "stop_id": "STOP2",
                "stop_sequence": "2",
            },
            {
                "trip_id": "TRIP_OVERNIGHT_2505",
                "arrival_time": "25:05:00",
                "departure_time": "25:05:00",
                "stop_id": "STOP1",
                "stop_sequence": "1",
            },
            {
                "trip_id": "TRIP_OVERNIGHT_2505",
                "arrival_time": "25:20:00",
                "departure_time": "25:20:00",
                "stop_id": "STOP2",
                "stop_sequence": "2",
            },
        ],
        calendars=[
            {
                "service_id": "WEEKDAY",
                "monday": "1",
                "tuesday": "1",
                "wednesday": "1",
                "thursday": "1",
                "friday": "1",
                "saturday": "0",
                "sunday": "0",
                "start_date": "20250101",
                "end_date": "20291231",
            },
        ],
        calendar_dates=[
            {
                "service_id": "WEEKDAY",
                "date": "20260318",
                "exception_type": "2",
            },
            {
                "service_id": "SPECIAL_EVENT",
                "date": "20260318",
                "exception_type": "1",
            },
        ],
    )


def make_test_settings(project_root: Path) -> Settings:
    return Settings(
        project_root=project_root,
        instance_path=project_root / "instance",
        data_dir=project_root / "data",
        db_path=(project_root / "instance" / "saved_locations.sqlite"),
        flask_env="testing",
        secret_key="test-secret-key",
        host="127.0.0.1",
        port=5000,
        debug=False,
        cdta_gtfs_url="https://example.invalid/google_transit.zip",
        http_timeout_seconds=5.0,
        http_user_agent="cdta-local-lookup-tests/1.0",
        nearby_stop_limit=5,
        nearby_stop_radius_miles=0.75,
        enable_realtime=False,
        realtime_trip_updates_url=None,
        realtime_api_key=None,
        realtime_vehicle_positions_url=None,
    )


def _write_csv(archive: zipfile.ZipFile, member_name: str, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"{member_name} requires at least one row in tests.")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    archive.writestr(member_name, buffer.getvalue())
