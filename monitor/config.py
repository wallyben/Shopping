"""
Loads and validates configuration from .env and skus.json.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent


@dataclass
class SKU:
    name: str
    url: str
    sku: str
    enabled: bool
    notes: str = ""

    def display(self) -> str:
        return f"{self.name} ({self.sku})"


@dataclass
class Config:
    # Notifications
    discord_webhook_url: Optional[str]
    telegram_bot_token: Optional[str]
    telegram_chat_id: Optional[str]

    # Polling
    poll_interval: float          # normal interval, seconds
    poll_interval_fast: float     # fast interval when product was recently OOS
    poll_jitter_max: float        # max random jitter added per poll

    # Behaviour
    auto_open_browser: bool
    http_proxy: Optional[str]

    # SKUs loaded from skus.json
    skus: list[SKU] = field(default_factory=list)

    @property
    def has_notifier(self) -> bool:
        return bool(self.discord_webhook_url or
                    (self.telegram_bot_token and self.telegram_chat_id))

    @classmethod
    def load(cls) -> "Config":
        skus_path = ROOT / "skus.json"
        if not skus_path.exists():
            raise FileNotFoundError(f"skus.json not found at {skus_path}")

        raw_skus = json.loads(skus_path.read_text())
        skus = [
            SKU(
                name=s["name"],
                url=s["url"],
                sku=s["sku"],
                enabled=s.get("enabled", True),
                notes=s.get("notes", ""),
            )
            for s in raw_skus
        ]

        cfg = cls(
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            poll_interval=float(os.getenv("POLL_INTERVAL_SECONDS", "30")),
            poll_interval_fast=float(os.getenv("POLL_INTERVAL_FAST_SECONDS", "10")),
            poll_jitter_max=float(os.getenv("POLL_JITTER_MAX_SECONDS", "5")),
            auto_open_browser=os.getenv("AUTO_OPEN_BROWSER", "1") == "1",
            http_proxy=os.getenv("HTTP_PROXY") or None,
            skus=skus,
        )

        return cfg

    def active_skus(self) -> list[SKU]:
        return [s for s in self.skus if s.enabled]
