"""Postgres access for flood reports. Connection string comes from DATABASE_URL."""

from datetime import datetime
import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id              SERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    captured_at     TIMESTAMPTZ,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    accuracy_m      DOUBLE PRECISION,
    location_source TEXT NOT NULL,
    photo           BYTEA NOT NULL,
    photo_mime      TEXT NOT NULL,
    candidates      JSONB NOT NULL,
    note            TEXT
)
"""

# Columns added after the table was first deployed.
MIGRATIONS = [
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS note TEXT",
]


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def init_db() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)
        for statement in MIGRATIONS:
            conn.execute(statement)


def insert_report(
    *,
    captured_at: datetime | None,
    lat: float,
    lon: float,
    accuracy_m: float | None,
    location_source: str,
    photo: bytes,
    photo_mime: str,
    candidates: list[dict],
    note: str | None,
) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO reports
                (captured_at, lat, lon, accuracy_m, location_source, photo, photo_mime,
                 candidates, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (captured_at, lat, lon, accuracy_m, location_source, photo, photo_mime,
             Jsonb(candidates), note),
        ).fetchone()
    return row["id"]


def list_reports() -> list[dict]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT id, received_at, captured_at, lat, lon, accuracy_m, location_source,
                   candidates, note
            FROM reports ORDER BY received_at DESC
            """
        ).fetchall()


def clear_reports() -> int:
    """Delete every report and restart IDs at 1. Returns the number deleted."""
    with connect() as conn:
        count = conn.execute("SELECT count(*) AS n FROM reports").fetchone()["n"]
        conn.execute("TRUNCATE reports RESTART IDENTITY")
    return count


def get_photo(report_id: int) -> dict | None:
    with connect() as conn:
        return conn.execute(
            "SELECT photo, photo_mime FROM reports WHERE id = %s", (report_id,)
        ).fetchone()
