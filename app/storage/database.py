"""
SQLite storage layer.

Schema auto-created on first run.
All data stored in the app directory (portable: no AppData, no registry).

Thread safety: each call opens/closes its own connection. The DB file is
in WAL mode so concurrent readers and one writer do not block each other.
The monitor runs in a background thread that never touches this module
directly — it reads config once at start and the UI thread owns all DB writes.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).resolve().parents[2]
DB_PATH = APP_DIR / "monitor_data.db"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Product:
    id: int
    name: str
    url: str
    sku: str
    enabled: bool
    notes: str
    created_at: str
    last_status: str   # StockState.value string
    last_checked: Optional[str]

    @property
    def last_checked_display(self) -> str:
        if not self.last_checked:
            return "Never"
        try:
            dt = datetime.fromisoformat(self.last_checked)
            return dt.strftime("%H:%M:%S")
        except ValueError:
            return self.last_checked


@dataclass
class Settings:
    poll_interval: float = 30.0
    poll_interval_fast: float = 10.0
    poll_jitter_max: float = 5.0
    auto_open_browser: bool = True
    http_proxy: str = ""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    url          TEXT    NOT NULL UNIQUE,
    sku          TEXT    NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    notes        TEXT    DEFAULT '',
    created_at   TEXT    NOT NULL,
    last_status  TEXT    DEFAULT 'UNKNOWN',
    last_checked TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create schema if not present. Safe to call on every launch."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

def _row_to_product(row: sqlite3.Row) -> Product:
    return Product(
        id=row["id"],
        name=row["name"],
        url=row["url"],
        sku=row["sku"],
        enabled=bool(row["enabled"]),
        notes=row["notes"] or "",
        created_at=row["created_at"],
        last_status=row["last_status"] or "UNKNOWN",
        last_checked=row["last_checked"],
    )


def get_all_products() -> list[Product]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM products ORDER BY created_at ASC"
        ).fetchall()
    return [_row_to_product(r) for r in rows]


def get_enabled_products() -> list[Product]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE enabled=1 ORDER BY created_at ASC"
        ).fetchall()
    return [_row_to_product(r) for r in rows]


def add_product(name: str, url: str, sku: str = "", notes: str = "") -> Product:
    now = datetime.now().isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO products (name, url, sku, enabled, notes, created_at)
               VALUES (?, ?, ?, 1, ?, ?)""",
            (name.strip(), url.strip(), sku.strip(), notes.strip(), now),
        )
        product_id = cur.lastrowid
    return get_product_by_id(product_id)


def delete_product(product_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM products WHERE id=?", (product_id,))


def set_product_enabled(product_id: int, enabled: bool) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE products SET enabled=? WHERE id=?",
            (1 if enabled else 0, product_id),
        )


def update_product_status(url: str, status: str) -> None:
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE products SET last_status=?, last_checked=? WHERE url=?",
            (status, now, url),
        )


def get_product_by_id(product_id: int) -> Optional[Product]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id=?", (product_id,)
        ).fetchone()
    return _row_to_product(row) if row else None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def load_settings() -> Settings:
    return Settings(
        poll_interval=float(get_setting("poll_interval", "30")),
        poll_interval_fast=float(get_setting("poll_interval_fast", "10")),
        poll_jitter_max=float(get_setting("poll_jitter_max", "5")),
        auto_open_browser=get_setting("auto_open_browser", "1") == "1",
        http_proxy=get_setting("http_proxy", ""),
    )


def save_settings(s: Settings) -> None:
    set_setting("poll_interval", str(s.poll_interval))
    set_setting("poll_interval_fast", str(s.poll_interval_fast))
    set_setting("poll_jitter_max", str(s.poll_jitter_max))
    set_setting("auto_open_browser", "1" if s.auto_open_browser else "0")
    set_setting("http_proxy", s.http_proxy or "")
