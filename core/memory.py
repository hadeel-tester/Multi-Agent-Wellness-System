"""SQLite persistence for user health profiles across sessions.

Schema
------
user_profiles
  user_id              TEXT PRIMARY KEY
  name                 TEXT
  age                  INTEGER
  sex                  TEXT
  weight_kg            REAL
  height_cm            REAL
  health_goals         TEXT  (JSON list/string)
  dietary_restrictions TEXT  (JSON list)
  allergies            TEXT  (JSON list)
  calorie_target       INTEGER
  created_at           TEXT  (ISO-8601)
  updated_at           TEXT  (ISO-8601)

JSON fields (health_goals, dietary_restrictions, allergies) are serialised on
write and deserialised back to Python objects on read.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

# CAPSTONE: Add user authentication and session management for multi-user production use.

_DB_PATH = os.getenv("MEMORY_DB_PATH", "data/user_profiles.db")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id              TEXT PRIMARY KEY,
    name                 TEXT,
    age                  INTEGER,
    sex                  TEXT,
    weight_kg            REAL,
    height_cm            REAL,
    health_goals         TEXT,
    dietary_restrictions TEXT,
    allergies            TEXT,
    calorie_target       INTEGER,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
"""

_JSON_FIELDS = ("health_goals", "dietary_restrictions", "allergies")


def _get_db_path() -> str:
    """Return the DB path, re-reading the env var each call so tests can override it."""
    return os.getenv("MEMORY_DB_PATH", "data/user_profiles.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """Create the user_profiles table if it does not exist.

    Call once at application startup (e.g. in app.py before launching the UI).
    Creates the data/ directory if needed.
    """
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(_CREATE_TABLE_SQL)
        # Migrate existing DBs that predate the sex column
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(user_profiles)")}
        if "sex" not in existing_cols:
            conn.execute("ALTER TABLE user_profiles ADD COLUMN sex TEXT")
        conn.commit()


def save_profile(user_id: str, profile: dict) -> None:
    """Upsert a user's health profile in SQLite (INSERT OR REPLACE).

    JSON fields (health_goals, dietary_restrictions, allergies) are serialised
    automatically. created_at is preserved on update; updated_at is always
    refreshed to the current UTC time.

    Args:
        user_id: Unique identifier for the user.
        profile: Dict with any subset of the schema fields.
    """
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)

    now = _now()

    # Serialise JSON fields
    serialised = {k: v for k, v in profile.items()}
    for field in _JSON_FIELDS:
        if field in serialised and not isinstance(serialised[field], str):
            serialised[field] = json.dumps(serialised[field])

    with sqlite3.connect(db_path) as conn:
        # Preserve created_at if the row already exists
        existing = conn.execute(
            "SELECT created_at FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        created_at = existing[0] if existing else now

        conn.execute(
            """
            INSERT OR REPLACE INTO user_profiles
                (user_id, name, age, sex, weight_kg, height_cm,
                 health_goals, dietary_restrictions, allergies,
                 calorie_target, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                serialised.get("name"),
                serialised.get("age"),
                serialised.get("sex"),
                serialised.get("weight_kg"),
                serialised.get("height_cm"),
                serialised.get("health_goals"),
                serialised.get("dietary_restrictions"),
                serialised.get("allergies"),
                serialised.get("calorie_target"),
                created_at,
                now,
            ),
        )
        conn.commit()


def load_profile(user_id: str) -> dict | None:
    """Load a user's health profile from SQLite.

    Deserialises JSON fields back to Python lists/dicts.

    Args:
        user_id: Unique identifier for the user.

    Returns:
        Profile dict if found, None otherwise.
    """
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return None

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()

    if row is None:
        return None

    profile = dict(row)

    # Deserialise JSON fields
    for field in _JSON_FIELDS:
        raw = profile.get(field)
        if raw is not None:
            try:
                profile[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass  # leave as-is if not valid JSON

    return profile


def delete_profile(user_id: str) -> None:
    """Delete a user's health profile from SQLite.

    Args:
        user_id: Unique identifier for the user.
    """
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))
        conn.commit()
