"""
Notification dispatcher.

Supports:
  - Discord webhook
  - Telegram Bot API

Both are fired concurrently via asyncio.gather so neither blocks the other.
Failure of one notifier does not suppress the other.

No third-party SDK used for Telegram — raw httpx calls keep the dependency
surface minimal and avoid SDK version churn.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from .config import Config, SKU

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._client = httpx.AsyncClient(timeout=10.0)

    async def send_restock_alert(self, sku: SKU, previous_state: str) -> None:
        """
        Fire all configured notification channels concurrently.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        message = (
            f"RESTOCK DETECTED\n"
            f"Product : {sku.name}\n"
            f"SKU     : {sku.sku}\n"
            f"URL     : {sku.url}\n"
            f"Time    : {timestamp}\n"
            f"Prev    : {previous_state} -> IN_STOCK\n"
            f"\nOpen the URL above and complete checkout manually."
        )

        tasks = []
        if self._cfg.discord_webhook_url:
            tasks.append(self._send_discord(message, sku.url))
        if self._cfg.telegram_bot_token and self._cfg.telegram_chat_id:
            tasks.append(self._send_telegram(message))

        if not tasks:
            logger.warning("No notifiers configured — alert not sent for %s", sku.name)
            return

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Notifier %d failed: %s", i, result)

    async def send_status_update(self, message: str) -> None:
        """
        Send a plain status/error message to all channels.
        """
        tasks = []
        if self._cfg.discord_webhook_url:
            tasks.append(self._send_discord(message, url=None))
        if self._cfg.telegram_bot_token and self._cfg.telegram_chat_id:
            tasks.append(self._send_telegram(message))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_discord(self, message: str, url: Optional[str]) -> None:
        embed = {
            "title": "RESTOCK ALERT — Pokemon Center UK",
            "description": f"```\n{message}\n```",
            "color": 0xFF0000,
            "url": url,
        }
        payload: dict = {"embeds": [embed]}

        resp = await self._client.post(
            self._cfg.discord_webhook_url,
            json=payload,
        )
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Discord webhook returned {resp.status_code}: {resp.text[:200]}")
        logger.info("Discord notification sent")

    async def _send_telegram(self, message: str) -> None:
        api_url = (
            f"https://api.telegram.org/bot{self._cfg.telegram_bot_token}/sendMessage"
        )
        payload = {
            "chat_id": self._cfg.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        resp = await self._client.post(api_url, json=payload)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        logger.info("Telegram notification sent")

    async def close(self):
        await self._client.aclose()
