from __future__ import annotations

import argparse
import logging
from typing import Any

from flask import Flask, flash, redirect, render_template, request, url_for

from services.cdta import CDTAService, GTFSDownloadError, GTFSLoadError, ResolvedLocation
from services.geocode import CensusGeocoder, GeocodeError
from services.gtfs_loader import GTFSLoader
from services.locations import DuplicateNicknameError, LocationStore, SavedLocationNotFoundError
from utils.config import Settings, load_settings
from utils.geo import format_coordinates
from utils.validation import ValidationError, parse_coordinate_fields, parse_lat_lon_input, require_text


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or load_settings()
    settings.instance_path.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, instance_path=str(settings.instance_path), instance_relative_config=False)
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["SETTINGS"] = settings

    location_store = LocationStore(settings.db_path)
    location_store.initialize()
    geocoder = CensusGeocoder(
        timeout_seconds=settings.http_timeout_seconds,
        user_agent=settings.http_user_agent,
    )
    gtfs_loader = GTFSLoader(
        feed_url=settings.cdta_gtfs_url,
        data_dir=settings.data_dir,
        timeout_seconds=settings.http_timeout_seconds,
        user_agent=settings.http_user_agent,
    )
    cdta_service = CDTAService(
        gtfs_loader,
        nearby_stop_limit=settings.nearby_stop_limit,
        nearby_stop_radius_miles=settings.nearby_stop_radius_miles,
    )

    app.extensions["location_store"] = location_store
    app.extensions["geocoder"] = geocoder
    app.extensions["cdta_service"] = cdta_service

    @app.context_processor
    def inject_global_template_values() -> dict[str, Any]:
        return {
            "app_name": "CDTA Local Lookup",
            "feed_status": cdta_service.get_feed_status(),
            "realtime_enabled": settings.enable_realtime,
        }

    def render_home(
        *,
        form_data: dict[str, str] | None = None,
        errors: dict[str, str] | None = None,
        status_code: int = 200,
    ):
        payload = {
            "origin_mode": "current",
            "destination_mode": "typed",
        }
        if form_data:
            payload.update(form_data)
        return (
            render_template(
                "index.html",
                saved_locations=location_store.list_locations(),
                form_data=payload,
                errors=errors or {},
            ),
            status_code,
        )

    def resolve_location(scope: str, form_data: dict[str, str]) -> ResolvedLocation:
        mode = form_data.get(f"{scope}_mode", "typed")
        scope_name = scope.title()

        if mode == "current":
            latitude, longitude = parse_coordinate_fields(
                form_data.get(f"{scope}_current_lat"),
                form_data.get(f"{scope}_current_lon"),
                field_name=f"{scope_name} current location",
            )
            label = form_data.get(f"{scope}_current_label", "").strip() or f"{scope_name} browser location"
            return ResolvedLocation(
                source_type="current",
                label=label,
                latitude=latitude,
                longitude=longitude,
            )

        if mode == "saved":
            saved_id_text = require_text(form_data.get(f"{scope}_saved_id"), f"{scope_name} saved location")
            try:
                saved_id = int(saved_id_text)
            except ValueError as exc:
                raise ValidationError(f"{scope_name} saved location is invalid.") from exc

            saved_location = location_store.get_location(saved_id)
            return ResolvedLocation(
                source_type="saved",
                label=saved_location.original_label,
                latitude=saved_location.latitude,
                longitude=saved_location.longitude,
                nickname=saved_location.nickname,
                saved_location_id=saved_location.id,
            )

        typed_input = require_text(form_data.get(f"{scope}_typed_input"), f"{scope_name} location", max_length=200)
        lat_lon = parse_lat_lon_input(typed_input)
        if lat_lon is not None:
            latitude, longitude = lat_lon
            return ResolvedLocation(
                source_type="typed",
                label=f"Coordinates {format_coordinates(latitude, longitude)}",
                latitude=latitude,
                longitude=longitude,
            )

        match = geocoder.geocode(typed_input)
        return ResolvedLocation(
            source_type="typed",
            label=match.label,
            latitude=match.latitude,
            longitude=match.longitude,
        )

    def resolve_location_input_for_save(location_input: str) -> tuple[str, float, float]:
        lat_lon = parse_lat_lon_input(location_input)
        if lat_lon is not None:
            latitude, longitude = lat_lon
            return f"Coordinates {format_coordinates(latitude, longitude)}", latitude, longitude

        match = geocoder.geocode(location_input)
        return match.label, match.latitude, match.longitude

    @app.get("/")
    def index():
        return render_home()

    @app.post("/search")
    def search():
        form_data = request.form.to_dict(flat=True)
        errors: dict[str, str] = {}

        origin: ResolvedLocation | None = None
        destination: ResolvedLocation | None = None

        try:
            origin = resolve_location("origin", form_data)
        except (ValidationError, SavedLocationNotFoundError, GeocodeError) as exc:
            errors["origin"] = str(exc)

        try:
            destination = resolve_location("destination", form_data)
        except (ValidationError, SavedLocationNotFoundError, GeocodeError) as exc:
            errors["destination"] = str(exc)

        if errors:
            return render_home(form_data=form_data, errors=errors, status_code=400)

        if origin is None or destination is None:
            return render_home(
                form_data=form_data,
                errors={"form": "Could not resolve the requested locations."},
                status_code=400,
            )

        transit_result = None
        transit_error = None

        try:
            transit_result = cdta_service.lookup(origin, destination)
        except (GTFSDownloadError, GTFSLoadError) as exc:
            transit_error = str(exc)

        status_code = 503 if transit_error else 200
        return (
            render_template(
                "results.html",
                origin=origin,
                destination=destination,
                transit_result=transit_result,
                transit_error=transit_error,
            ),
            status_code,
        )

    @app.get("/saved-locations")
    def saved_locations():
        return render_template(
            "saved_locations.html",
            saved_locations=location_store.list_locations(),
        )

    @app.post("/saved-locations/create")
    def create_saved_location():
        nickname = request.form.get("nickname", "")
        location_input = request.form.get("location_input", "")

        try:
            label, latitude, longitude = resolve_location_input_for_save(location_input)
            location_store.create_location(nickname, label, latitude, longitude)
            flash("Saved location created.", "success")
        except (ValidationError, GeocodeError, DuplicateNicknameError) as exc:
            flash(str(exc), "error")

        return redirect(url_for("saved_locations"))

    @app.post("/saved-locations/<int:location_id>/update")
    def update_saved_location(location_id: int):
        nickname = request.form.get("nickname", "")
        try:
            location_store.update_nickname(location_id, nickname)
            flash("Nickname updated.", "success")
        except (ValidationError, DuplicateNicknameError, SavedLocationNotFoundError) as exc:
            flash(str(exc), "error")

        return redirect(url_for("saved_locations"))

    @app.post("/saved-locations/<int:location_id>/delete")
    def delete_saved_location(location_id: int):
        try:
            location_store.delete_location(location_id)
            flash("Saved location deleted.", "success")
        except SavedLocationNotFoundError as exc:
            flash(str(exc), "error")

        return redirect(url_for("saved_locations"))

    @app.post("/saved-locations/save-resolved")
    def save_resolved_location():
        nickname = request.form.get("nickname", "")
        label = request.form.get("label", "")

        try:
            latitude, longitude = parse_coordinate_fields(
                request.form.get("latitude"),
                request.form.get("longitude"),
                field_name="Saved location",
            )
            clean_label = require_text(label, "Resolved label", max_length=200)
            location_store.create_location(nickname, clean_label, latitude, longitude)
            flash("Location saved.", "success")
        except (ValidationError, DuplicateNicknameError) as exc:
            flash(str(exc), "error")

        return redirect(url_for("saved_locations"))

    @app.post("/refresh-gtfs")
    def refresh_gtfs():
        try:
            path = cdta_service.refresh_feed()
            flash(f"Refreshed GTFS cache at {path}.", "success")
        except GTFSDownloadError as exc:
            flash(str(exc), "error")

        return redirect(request.referrer or url_for("index"))

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="CDTA local-first route lookup")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["run", "refresh-gtfs"],
        default="run",
        help="Run the Flask app or refresh the GTFS cache.",
    )
    args = parser.parse_args()

    configured_app = app
    settings: Settings = configured_app.config["SETTINGS"]
    service: CDTAService = configured_app.extensions["cdta_service"]

    if args.command == "refresh-gtfs":
        path = service.refresh_feed()
        print(f"Refreshed GTFS cache at {path}")
        return

    configured_app.run(host=settings.host, port=settings.port, debug=settings.debug)


if __name__ == "__main__":
    main()
