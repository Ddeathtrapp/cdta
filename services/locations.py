from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from utils.validation import ValidationError, normalize_nickname, validate_coordinate_pair, validate_nickname


class DuplicateNicknameError(ValidationError):
    """Raised when a nickname already exists."""


class SavedLocationNotFoundError(ValidationError):
    """Raised when a saved location does not exist."""


@dataclass(frozen=True)
class SavedLocation:
    id: int
    nickname: str
    original_label: str
    latitude: float
    longitude: float
    created_at: str
    updated_at: str


class LocationStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nickname TEXT NOT NULL,
                    nickname_normalized TEXT NOT NULL UNIQUE,
                    original_label TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def list_locations(self) -> list[SavedLocation]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, nickname, original_label, latitude, longitude, created_at, updated_at
                FROM saved_locations
                ORDER BY nickname_normalized ASC
                """
            ).fetchall()
        return [self._row_to_location(row) for row in rows]

    def get_location(self, location_id: int) -> SavedLocation:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, nickname, original_label, latitude, longitude, created_at, updated_at
                FROM saved_locations
                WHERE id = ?
                """,
                (location_id,),
            ).fetchone()

        if row is None:
            raise SavedLocationNotFoundError("Saved location was not found.")
        return self._row_to_location(row)

    def get_location_by_nickname(self, nickname: str) -> SavedLocation:
        normalized_nickname = normalize_nickname(validate_nickname(nickname))
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, nickname, original_label, latitude, longitude, created_at, updated_at
                FROM saved_locations
                WHERE nickname_normalized = ?
                """,
                (normalized_nickname,),
            ).fetchone()

        if row is None:
            raise SavedLocationNotFoundError("Saved location was not found.")
        return self._row_to_location(row)

    def create_location(
        self,
        nickname: str,
        original_label: str,
        latitude: float,
        longitude: float,
    ) -> SavedLocation:
        clean_nickname = validate_nickname(nickname)
        validate_coordinate_pair(latitude, longitude)
        label = str(original_label).strip() or "Unnamed location"
        timestamp = self._timestamp()

        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO saved_locations (
                        nickname,
                        nickname_normalized,
                        original_label,
                        latitude,
                        longitude,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_nickname,
                        normalize_nickname(clean_nickname),
                        label,
                        latitude,
                        longitude,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateNicknameError("Nickname already exists. Please choose a different one.") from exc

            new_id = int(cursor.lastrowid)

        return self.get_location(new_id)

    def update_nickname(self, location_id: int, nickname: str) -> SavedLocation:
        clean_nickname = validate_nickname(nickname)
        timestamp = self._timestamp()

        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT id FROM saved_locations WHERE id = ?",
                (location_id,),
            )
            if cursor.fetchone() is None:
                raise SavedLocationNotFoundError("Saved location was not found.")

            try:
                connection.execute(
                    """
                    UPDATE saved_locations
                    SET nickname = ?, nickname_normalized = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        clean_nickname,
                        normalize_nickname(clean_nickname),
                        timestamp,
                        location_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateNicknameError("Nickname already exists. Please choose a different one.") from exc

        return self.get_location(location_id)

    def delete_location(self, location_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM saved_locations WHERE id = ?",
                (location_id,),
            )
            if cursor.rowcount == 0:
                raise SavedLocationNotFoundError("Saved location was not found.")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row_to_location(row: sqlite3.Row) -> SavedLocation:
        return SavedLocation(
            id=int(row["id"]),
            nickname=row["nickname"],
            original_label=row["original_label"],
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
