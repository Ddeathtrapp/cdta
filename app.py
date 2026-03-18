from __future__ import annotations

import argparse
import logging
import secrets
from datetime import datetime
from functools import wraps
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from services.cdta import CDTAService, GTFSDownloadError, GTFSLoadError, ResolvedLocation, TransitLookupResult
from services.geocode import GeocodeError, LocationGeocoder
from services.gtfs_loader import GTFSLoader, SERVICE_TIMEZONE
from services.locations import DuplicateNicknameError, LocationStore, SavedLocationNotFoundError, SavedLocation
from services.realtime import GTFSRealtimeService
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
    app.config["NOW_PROVIDER"] = lambda: datetime.now(tz=SERVICE_TIMEZONE)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = settings.flask_env == "production"

    location_store = LocationStore(settings.db_path)
    location_store.initialize()
    geocoder = LocationGeocoder(
        timeout_seconds=settings.http_timeout_seconds,
        user_agent=settings.http_user_agent,
    )
    gtfs_loader = GTFSLoader(
        feed_url=settings.cdta_gtfs_url,
        data_dir=settings.data_dir,
        timeout_seconds=settings.http_timeout_seconds,
        user_agent=settings.http_user_agent,
    )
    realtime_service = GTFSRealtimeService(
        enabled=settings.enable_realtime,
        trip_updates_url=settings.realtime_trip_updates_url,
        api_key=settings.realtime_api_key,
        timeout_seconds=settings.http_timeout_seconds,
        user_agent=settings.http_user_agent,
    )
    cdta_service = CDTAService(
        gtfs_loader,
        nearby_stop_limit=settings.nearby_stop_limit,
        nearby_stop_radius_miles=settings.nearby_stop_radius_miles,
        realtime_service=realtime_service,
    )

    app.extensions["location_store"] = location_store
    app.extensions["geocoder"] = geocoder
    app.extensions["cdta_service"] = cdta_service
    app.extensions["realtime_service"] = realtime_service

    def admin_login_enabled() -> bool:
        return bool(settings.admin_password)

    def admin_login_disabled_message() -> str:
        return "Admin login is disabled until ADMIN_PASSWORD is configured."

    def is_admin_authenticated() -> bool:
        return bool(admin_login_enabled() and session.get("is_admin") is True)

    def ensure_csrf_token() -> str:
        csrf_token = session.get("_csrf_token")
        if not csrf_token:
            csrf_token = secrets.token_urlsafe(32)
            session["_csrf_token"] = csrf_token
        return csrf_token

    def rotate_csrf_token() -> str:
        csrf_token = secrets.token_urlsafe(32)
        session["_csrf_token"] = csrf_token
        return csrf_token

    def validate_csrf_token() -> None:
        session_token = session.get("_csrf_token")
        request_token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not session_token or not request_token or not secrets.compare_digest(str(request_token), str(session_token)):
            raise ValidationError("A valid CSRF token is required.")

    def sanitize_next_url(raw_target: Any | None, *, default: str) -> str:
        if raw_target is None:
            return default

        target = str(raw_target).strip()
        if not target:
            return default

        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            return default
        if not target.startswith("/") or target.startswith("//"):
            return default
        return target

    def current_relative_url() -> str:
        query_string = request.query_string.decode("utf-8")
        return f"{request.path}?{query_string}" if query_string else request.path

    def render_admin_login(*, error: str | None = None, status_code: int = 200):
        return (
            render_template(
                "admin_login.html",
                login_error=error,
                next_url=sanitize_next_url(request.values.get("next"), default=url_for("saved_locations")),
            ),
            status_code,
        )

    @app.context_processor
    def inject_global_template_values() -> dict[str, Any]:
        return {
            "app_name": "CDTA Local Lookup",
            "feed_status": cdta_service.get_feed_status(),
            "realtime_enabled": settings.enable_realtime,
            "realtime_configured": bool(settings.enable_realtime and settings.realtime_trip_updates_url),
            "is_admin": is_admin_authenticated(),
            "admin_login_enabled": admin_login_enabled(),
            "admin_login_disabled_message": admin_login_disabled_message(),
            "csrf_token": ensure_csrf_token,
        }

    @app.after_request
    def add_admin_cache_headers(response):
        admin_paths = (
            "/admin/",
            "/saved-locations",
            "/api/saved-locations",
            "/refresh-gtfs",
        )
        if request.path.startswith(admin_paths):
            response.headers["Cache-Control"] = "no-store, no-cache, max-age=0, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

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
                saved_locations=location_store.list_locations() if is_admin_authenticated() else [],
                form_data=payload,
                errors=errors or {},
            ),
            status_code,
        )

    def build_results_query(form_data: dict[str, str]) -> dict[str, str]:
        query: dict[str, str] = {}
        for scope in ("origin", "destination"):
            mode = form_data.get(f"{scope}_mode", "typed")
            query[f"{scope}_mode"] = mode
            if mode == "current":
                for suffix in ("current_lat", "current_lon", "current_label"):
                    key = f"{scope}_{suffix}"
                    value = form_data.get(key, "")
                    if value:
                        query[key] = value
            elif mode == "saved":
                key = f"{scope}_saved_id"
                value = form_data.get(key, "")
                if value:
                    query[key] = value
            else:
                key = f"{scope}_typed_input"
                value = form_data.get(key, "")
                if value:
                    query[key] = value
        return query

    def resolved_from_saved_location(saved_location: SavedLocation) -> ResolvedLocation:
        return ResolvedLocation(
            source_type="saved",
            label=saved_location.original_label,
            latitude=saved_location.latitude,
            longitude=saved_location.longitude,
            nickname=saved_location.nickname,
            saved_location_id=saved_location.id,
        )

    def resolve_saved_reference(
        scope_name: str,
        *,
        saved_id: Any | None = None,
        nickname: Any | None = None,
        reference: Any | None = None,
    ) -> ResolvedLocation:
        if saved_id is not None and saved_id != "":
            saved_id_text = require_text(saved_id, f"{scope_name} saved location")
            try:
                location_id = int(saved_id_text)
            except ValueError as exc:
                raise ValidationError(f"{scope_name} saved location is invalid.") from exc
            return resolved_from_saved_location(location_store.get_location(location_id))

        if nickname is not None and nickname != "":
            clean_nickname = require_text(nickname, f"{scope_name} saved nickname", max_length=80)
            return resolved_from_saved_location(location_store.get_location_by_nickname(clean_nickname))

        if reference is not None and reference != "":
            saved_reference = require_text(reference, f"{scope_name} saved location", max_length=80)
            if saved_reference.isdigit():
                return resolved_from_saved_location(location_store.get_location(int(saved_reference)))
            return resolved_from_saved_location(location_store.get_location_by_nickname(saved_reference))

        raise ValidationError(f"{scope_name} saved location is required.")

    def resolve_typed_location(scope_name: str, typed_input: Any) -> ResolvedLocation:
        clean_input = require_text(typed_input, f"{scope_name} location", max_length=200)
        lat_lon = parse_lat_lon_input(clean_input)
        if lat_lon is not None:
            latitude, longitude = lat_lon
            return ResolvedLocation(
                source_type="typed",
                label=f"Coordinates {format_coordinates(latitude, longitude)}",
                latitude=latitude,
                longitude=longitude,
            )

        match = geocoder.geocode(clean_input)
        return ResolvedLocation(
            source_type="typed",
            label=match.label,
            latitude=match.latitude,
            longitude=match.longitude,
        )

    def resolve_location_spec(
        scope_name: str,
        *,
        mode: str,
        latitude: Any | None = None,
        longitude: Any | None = None,
        label: Any | None = None,
        typed_input: Any | None = None,
        saved_id: Any | None = None,
        nickname: Any | None = None,
        saved_reference: Any | None = None,
    ) -> ResolvedLocation:
        normalized_mode = require_text(mode, f"{scope_name} mode", max_length=20).lower()

        if normalized_mode == "current":
            resolved_latitude, resolved_longitude = parse_coordinate_fields(
                latitude,
                longitude,
                field_name=f"{scope_name} current location",
            )
            clean_label = str(label or "").strip() or f"{scope_name} provided coordinates"
            return ResolvedLocation(
                source_type="current",
                label=clean_label,
                latitude=resolved_latitude,
                longitude=resolved_longitude,
            )

        if normalized_mode == "saved":
            return resolve_saved_reference(
                scope_name,
                saved_id=saved_id,
                nickname=nickname,
                reference=saved_reference,
            )

        if normalized_mode == "typed":
            return resolve_typed_location(scope_name, typed_input)

        raise ValidationError(f"{scope_name} mode is invalid.")

    def resolve_location(scope: str, form_data: dict[str, str]) -> ResolvedLocation:
        scope_name = scope.title()
        mode = form_data.get(f"{scope}_mode", "typed")

        if mode == "current":
            return resolve_location_spec(
                scope_name,
                mode="current",
                latitude=form_data.get(f"{scope}_current_lat"),
                longitude=form_data.get(f"{scope}_current_lon"),
                label=form_data.get(f"{scope}_current_label", "").strip() or f"{scope_name} browser location",
            )

        if mode == "saved":
            return resolve_location_spec(
                scope_name,
                mode="saved",
                saved_id=form_data.get(f"{scope}_saved_id"),
            )

        return resolve_location_spec(
            scope_name,
            mode="typed",
            typed_input=form_data.get(f"{scope}_typed_input"),
        )

    def resolve_location_input_for_save(location_input: str) -> tuple[str, float, float]:
        lat_lon = parse_lat_lon_input(location_input)
        if lat_lon is not None:
            latitude, longitude = lat_lon
            return f"Coordinates {format_coordinates(latitude, longitude)}", latitude, longitude

        match = geocoder.geocode(location_input)
        return match.label, match.latitude, match.longitude

    def parse_positive_int(value: Any, field_name: str, *, default: int, maximum: int) -> int:
        if value is None or value == "":
            return default

        raw_text = require_text(value, field_name, max_length=12)
        try:
            parsed_value = int(raw_text)
        except ValueError as exc:
            raise ValidationError(f"{field_name} must be a whole number.") from exc

        if parsed_value < 1 or parsed_value > maximum:
            raise ValidationError(f"{field_name} must be between 1 and {maximum}.")
        return parsed_value

    def api_error(message: str, status_code: int, *, code: str, details: dict[str, Any] | None = None):
        payload: dict[str, Any] = {
            "error": {
                "code": code,
                "message": message,
                "status": status_code,
            }
        }
        if details:
            payload["error"]["details"] = details
        response = jsonify(payload)
        response.status_code = status_code
        return response

    def auth_error_response(message: str, status_code: int, *, code: str):
        if request.path.startswith("/api/"):
            return api_error(message, status_code, code=code)
        return message, status_code

    def admin_required(*, redirect_to_login: bool = False):
        def decorator(view_func):
            @wraps(view_func)
            def wrapped(*args, **kwargs):
                if is_admin_authenticated():
                    return view_func(*args, **kwargs)

                if redirect_to_login:
                    return redirect(
                        url_for(
                            "admin_login",
                            next=current_relative_url(),
                        )
                    )

                message = admin_login_disabled_message() if not admin_login_enabled() else "Admin login required."
                status_code = 503 if not admin_login_enabled() else 401
                return auth_error_response(message, status_code, code="admin_required")

            return wrapped

        return decorator

    def csrf_protected(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            try:
                validate_csrf_token()
            except ValidationError as exc:
                return auth_error_response(str(exc), 400, code="invalid_csrf")
            return view_func(*args, **kwargs)

        return wrapped

    def status_code_for_geocode_error(exc: GeocodeError) -> int:
        if "Could not reach" in str(exc):
            return 502
        if "No nearby address was found" in str(exc):
            return 404
        return 400

    def status_code_for_resolution_error(exc: Exception) -> int:
        if isinstance(exc, SavedLocationNotFoundError):
            return 404
        if isinstance(exc, GeocodeError):
            return status_code_for_geocode_error(exc)
        return 400

    def serialize_saved_location(saved_location: SavedLocation) -> dict[str, Any]:
        return {
            "id": saved_location.id,
            "nickname": saved_location.nickname,
            "original_label": saved_location.original_label,
            "latitude": saved_location.latitude,
            "longitude": saved_location.longitude,
            "created_at": saved_location.created_at,
            "updated_at": saved_location.updated_at,
        }

    def serialize_departure(departure) -> dict[str, Any]:
        return {
            "trip_id": departure.trip_id,
            "headsign": departure.headsign,
            "scheduled_departure": departure.scheduled_departure.isoformat(),
            "realtime_departure": departure.realtime_departure.isoformat() if departure.realtime_departure else None,
            "effective_departure": departure.effective_departure.isoformat(),
            "minutes": departure.minutes_until_departure,
            "display_wait": departure.display_wait,
            "display_departure_time": departure.display_departure_time,
            "display_summary": departure.display_summary,
            "source": departure.source,
            "delay_seconds": departure.delay_seconds,
        }

    def serialize_route(route, next_departures: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "route_id": route.route_id,
            "short_name": route.short_name,
            "long_name": route.long_name,
            "display_name": route.display_name,
            "next_departures": next_departures,
        }

    def serialize_stop_result(stop_result) -> dict[str, Any]:
        departure_groups = {group.route.route_id: group for group in stop_result.route_departures}
        routes_payload = []
        for route in stop_result.routes:
            group = departure_groups.get(route.route_id)
            departures_payload = [serialize_departure(departure) for departure in group.departures] if group else []
            route_payload = serialize_route(route, departures_payload)
            if group and group.headsign:
                route_payload["headsign"] = group.headsign
            routes_payload.append(route_payload)

        return {
            "stop_id": stop_result.stop.stop_id,
            "stop_name": stop_result.stop.name,
            "distance_miles": round(stop_result.distance_miles, 2),
            "within_radius": stop_result.within_radius,
            "departure_source_note": stop_result.departures_summary_label,
            "routes": routes_payload,
        }

    def serialize_lookup_result(
        origin: ResolvedLocation,
        destination: ResolvedLocation,
        transit_result: TransitLookupResult,
    ) -> dict[str, Any]:
        return {
            "origin": {
                "label": origin.label,
                "source_type": origin.source_type,
                "nickname": origin.nickname,
                "saved_location_id": origin.saved_location_id,
                "latitude": origin.latitude,
                "longitude": origin.longitude,
            },
            "destination": {
                "label": destination.label,
                "source_type": destination.source_type,
                "nickname": destination.nickname,
                "saved_location_id": destination.saved_location_id,
                "latitude": destination.latitude,
                "longitude": destination.longitude,
            },
            "origin_nearest_stops": [serialize_stop_result(stop_result) for stop_result in transit_result.origin_stops],
            "destination_nearest_stops": [
                serialize_stop_result(stop_result) for stop_result in transit_result.destination_stops
            ],
            "shared_routes": [
                {
                    "route_id": route.route_id,
                    "short_name": route.short_name,
                    "long_name": route.long_name,
                    "display_name": route.display_name,
                }
                for route in transit_result.shared_routes
            ],
            "direct_candidates": [
                {
                    "route_id": candidate.route.route_id,
                    "display_name": candidate.route.display_name,
                    "origin_stop_id": candidate.origin_stop.stop_id,
                    "origin_stop_name": candidate.origin_stop.name,
                    "destination_stop_id": candidate.destination_stop.stop_id,
                    "destination_stop_name": candidate.destination_stop.name,
                    "origin_distance_miles": round(candidate.origin_distance_miles, 2),
                    "destination_distance_miles": round(candidate.destination_distance_miles, 2),
                    "trip_headsign": candidate.trip_headsign,
                    "stop_gap": candidate.stop_gap,
                }
                for candidate in transit_result.direct_candidates
            ],
            "realtime_used": transit_result.realtime_used,
            "checked_at_display": transit_result.checked_at_text,
            "destination_serviceable": transit_result.destination_serviceable,
            "destination_service_message": transit_result.destination_service_message,
            "generated_at": transit_result.generated_at.isoformat(),
            "note": transit_result.note,
        }

    def render_results_from_form_data(form_data: dict[str, str]):
        refresh_url = url_for("results", **build_results_query(form_data))
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
            transit_result = cdta_service.lookup(
                origin,
                destination,
                now=app.config["NOW_PROVIDER"](),
            )
        except (GTFSDownloadError, GTFSLoadError) as exc:
            transit_error = str(exc)

        status_code = 503 if transit_error else 200
        return (
            render_template(
                "results.html",
                origin=origin,
                destination=destination,
                refresh_url=refresh_url,
                transit_result=transit_result,
                transit_error=transit_error,
            ),
            status_code,
        )

    def resolve_api_location_from_query(scope: str) -> ResolvedLocation:
        scope_name = scope.title()
        latitude = request.args.get(f"{scope}_lat")
        longitude = request.args.get(f"{scope}_lon")
        typed_input = request.args.get(f"{scope}_query")
        saved_reference = request.args.get(f"{scope}_saved")
        label = request.args.get(f"{scope}_label")

        if (latitude is not None and latitude != "") or (longitude is not None and longitude != ""):
            return resolve_location_spec(
                scope_name,
                mode="current",
                latitude=latitude,
                longitude=longitude,
                label=label or f"{scope_name} provided coordinates",
            )

        if saved_reference is not None and saved_reference != "":
            return resolve_location_spec(
                scope_name,
                mode="saved",
                saved_reference=saved_reference,
            )

        if typed_input is not None and typed_input != "":
            return resolve_location_spec(
                scope_name,
                mode="typed",
                typed_input=typed_input,
            )

        raise ValidationError(
            f"{scope_name} lookup parameters are required. Provide coordinates, a query, or a saved location."
        )

    def resolve_api_location_from_payload(scope: str, payload: dict[str, Any]) -> ResolvedLocation:
        scope_name = scope.title()
        raw_location = payload.get(scope)
        if not isinstance(raw_location, dict):
            raise ValidationError(f"{scope_name} payload is required.")

        mode = raw_location.get("mode")
        return resolve_location_spec(
            scope_name,
            mode=mode,
            latitude=raw_location.get("latitude"),
            longitude=raw_location.get("longitude"),
            label=raw_location.get("label") or f"{scope_name} provided coordinates",
            typed_input=raw_location.get("query"),
            saved_id=raw_location.get("saved_id"),
            nickname=raw_location.get("nickname"),
        )

    @app.get("/admin/login")
    def admin_login():
        if is_admin_authenticated():
            return redirect(sanitize_next_url(request.args.get("next"), default=url_for("saved_locations")))
        if not admin_login_enabled():
            return render_admin_login(error=admin_login_disabled_message(), status_code=503)
        return render_admin_login()

    @app.post("/admin/login")
    @csrf_protected
    def admin_login_submit():
        if not admin_login_enabled():
            return render_admin_login(error=admin_login_disabled_message(), status_code=503)

        password = request.form.get("password", "")
        if not secrets.compare_digest(password, settings.admin_password or ""):
            return render_admin_login(error="Incorrect admin password.", status_code=401)

        next_url = sanitize_next_url(request.form.get("next"), default=url_for("saved_locations"))
        session.clear()
        session["is_admin"] = True
        rotate_csrf_token()
        flash("Admin session started.", "success")
        return redirect(next_url)

    @app.post("/admin/logout")
    @admin_required()
    @csrf_protected
    def admin_logout():
        session.clear()
        flash("Admin session ended.", "success")
        return redirect(url_for("index"))

    @app.get("/")
    def index():
        return render_home()

    @app.post("/search")
    def search():
        form_data = request.form.to_dict(flat=True)
        return redirect(url_for("results", **build_results_query(form_data)))

    @app.get("/results")
    def results():
        form_data = request.args.to_dict(flat=True)
        if not form_data:
            return redirect(url_for("index"))
        return render_results_from_form_data(form_data)

    @app.get("/saved-locations")
    @admin_required(redirect_to_login=True)
    def saved_locations():
        return render_template(
            "saved_locations.html",
            saved_locations=location_store.list_locations(),
        )

    @app.post("/saved-locations/create")
    @admin_required()
    @csrf_protected
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
    @admin_required()
    @csrf_protected
    def update_saved_location(location_id: int):
        nickname = request.form.get("nickname", "")
        try:
            location_store.update_nickname(location_id, nickname)
            flash("Nickname updated.", "success")
        except (ValidationError, DuplicateNicknameError, SavedLocationNotFoundError) as exc:
            flash(str(exc), "error")

        return redirect(url_for("saved_locations"))

    @app.post("/saved-locations/<int:location_id>/delete")
    @admin_required()
    @csrf_protected
    def delete_saved_location(location_id: int):
        try:
            location_store.delete_location(location_id)
            flash("Saved location deleted.", "success")
        except SavedLocationNotFoundError as exc:
            flash(str(exc), "error")

        return redirect(url_for("saved_locations"))

    @app.post("/saved-locations/save-resolved")
    @admin_required()
    @csrf_protected
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
    @admin_required()
    @csrf_protected
    def refresh_gtfs():
        try:
            path = cdta_service.refresh_feed()
            flash(f"Refreshed GTFS cache at {path}.", "success")
        except GTFSDownloadError as exc:
            flash(str(exc), "error")

        return redirect(request.referrer or url_for("index"))

    @app.get("/api/health")
    def api_health():
        return jsonify(
            {
                "status": "ok",
                "timestamp": datetime.now(tz=SERVICE_TIMEZONE).isoformat(),
            }
        )

    @app.get("/api/saved-locations")
    @admin_required()
    def api_saved_locations():
        return jsonify(
            {
                "saved_locations": [
                    serialize_saved_location(saved_location) for saved_location in location_store.list_locations()
                ],
                "generated_at": datetime.now(tz=SERVICE_TIMEZONE).isoformat(),
            }
        )

    @app.get("/api/reverse-geocode")
    def api_reverse_geocode():
        try:
            latitude, longitude = parse_coordinate_fields(
                request.args.get("latitude"),
                request.args.get("longitude"),
                field_name="Reverse geocode",
            )
            match = geocoder.reverse_geocode(latitude, longitude)
        except GeocodeError as exc:
            return api_error(
                str(exc),
                status_code_for_geocode_error(exc),
                code="reverse_geocode_failed",
            )
        except ValidationError as exc:
            return api_error(
                str(exc),
                400,
                code="invalid_request",
            )

        return jsonify(
            {
                "label": match.label,
                "latitude": match.latitude,
                "longitude": match.longitude,
                "provider": "nominatim",
            }
        )

    @app.route("/api/lookup", methods=["GET", "POST"])
    def api_lookup():
        try:
            if request.method == "GET":
                origin = resolve_api_location_from_query("origin")
                destination = resolve_api_location_from_query("destination")
                max_stops = parse_positive_int(
                    request.args.get("max_stops"),
                    "max_stops",
                    default=settings.nearby_stop_limit,
                    maximum=10,
                )
                max_departures_per_route = parse_positive_int(
                    request.args.get("max_departures_per_route"),
                    "max_departures_per_route",
                    default=2,
                    maximum=5,
                )
            else:
                payload = request.get_json(silent=True)
                if not isinstance(payload, dict):
                    return api_error(
                        "POST /api/lookup requires a JSON object body.",
                        400,
                        code="invalid_json",
                    )

                origin = resolve_api_location_from_payload("origin", payload)
                destination = resolve_api_location_from_payload("destination", payload)
                max_stops = parse_positive_int(
                    payload.get("max_stops"),
                    "max_stops",
                    default=settings.nearby_stop_limit,
                    maximum=10,
                )
                max_departures_per_route = parse_positive_int(
                    payload.get("max_departures_per_route"),
                    "max_departures_per_route",
                    default=2,
                    maximum=5,
                )

            transit_result = cdta_service.lookup(
                origin,
                destination,
                now=app.config["NOW_PROVIDER"](),
                max_stops=max_stops,
                max_departures_per_route=max_departures_per_route,
            )
        except (ValidationError, SavedLocationNotFoundError, GeocodeError) as exc:
            return api_error(
                str(exc),
                status_code_for_resolution_error(exc),
                code="invalid_request",
            )
        except (GTFSDownloadError, GTFSLoadError) as exc:
            return api_error(
                str(exc),
                503,
                code="gtfs_unavailable",
            )

        return jsonify(serialize_lookup_result(origin, destination, transit_result))

    return app


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

    configured_app = create_app()
    settings: Settings = configured_app.config["SETTINGS"]
    service: CDTAService = configured_app.extensions["cdta_service"]

    if args.command == "refresh-gtfs":
        path = service.refresh_feed()
        print(f"Refreshed GTFS cache at {path}")
        return

    configured_app.run(host=settings.host, port=settings.port, debug=settings.debug)


if __name__ == "__main__":
    main()
