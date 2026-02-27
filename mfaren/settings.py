import json
from .db import get_cursor


def _key_mode(mode):
    return f"mode:{mode}"


def get_settings(mode):
    with get_cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = ?", (_key_mode(mode),))
        row = cur.fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return {}


def set_settings(mode, data):
    payload = json.dumps(data, ensure_ascii=False)
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_key_mode(mode), payload),
        )


def get_setting(key, default=None):
    with get_cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        if not row:
            return default
        return row["value"]


def set_setting(key, value):
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
