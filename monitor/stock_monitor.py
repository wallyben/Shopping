"""
Core monitoring loop.

Design:
- Each SKU runs its own independent async polling loop (asyncio.gather).
- Polling interval adapts: fast when a product was recently OOS (likely to
  restock in a burst), normal otherwise.
- Jitter is added to every sleep to avoid thundering-herd patterns and to
  make request timing less machine-like.
- State machine per SKU:
    UNKNOWN -> first poll establishes baseline
    OUT_OF_STOCK -> polling continues at fast or normal interval
    IN_STOCK -> alert fired once, then continues monitoring for subsequent
                OOS->IN_STOCK transitions (catches multiple restock events)
    BLOCKED -> back-off 5 minutes, then retry
- Idempotency: alert fires only on OUT_OF_STOCK -> IN_STOCK transition,
  never on IN_STOCK -> IN_STOCK (prevents duplicate alerts after restart).
- Consecutive UNKNOWN results >5 triggers a warning notification (detector
  may be broken by a site structure change).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Dict

from .config import Config, SKU
from .detector import StockState, detect
from .fetcher import Fetcher
from .launcher import launch, print_terminal_alert
from .notifier import Notifier

logger = logging.getLogger(__name__)

_BLOCKED_BACKOFF = 300.0        # 5 minutes when Cloudflare blocks
_UNKNOWN_WARN_THRESHOLD = 5     # consecutive UNKNOWNs before warning


class SKUMonitor:
    """Monitors a single SKU."""

    def __init__(self, sku: SKU, cfg: Config, fetcher: Fetcher, notifier: Notifier):
        self.sku = sku
        self.cfg = cfg
        self.fetcher = fetcher
        self.notifier = notifier

        self._state: StockState = StockState.UNKNOWN
        self._consecutive_unknown = 0
        self._poll_count = 0

    async def run(self) -> None:
        logger.info("Starting monitor for: %s", self.sku.display())
        while True:
            await self._poll()
            delay = self._next_delay()
            logger.debug("%s next poll in %.1fs", self.sku.sku, delay)
            await asyncio.sleep(delay)

    async def _poll(self) -> None:
        self._poll_count += 1
        try:
            resp = await self.fetcher.get(self.sku.url)
            html = resp.text
        except Exception as exc:
            logger.error("[%s] Fetch error: %s", self.sku.sku, exc)
            return

        new_state = detect(html, self.sku.url)
        await self._handle_state_change(new_state)

    async def _handle_state_change(self, new_state: StockState) -> None:
        previous = self._state

        if new_state == StockState.UNKNOWN:
            self._consecutive_unknown += 1
            if self._consecutive_unknown >= _UNKNOWN_WARN_THRESHOLD:
                msg = (
                    f"WARNING: {self.sku.display()} has returned UNKNOWN state "
                    f"{self._consecutive_unknown} times in a row. "
                    f"The site structure may have changed. Check detector.py."
                )
                logger.warning(msg)
                await self.notifier.send_status_update(msg)
                self._consecutive_unknown = 0  # Reset to avoid spam
            self._state = new_state
            return

        self._consecutive_unknown = 0

        if new_state == StockState.BLOCKED:
            logger.warning("[%s] Blocked — backing off %ds", self.sku.sku, _BLOCKED_BACKOFF)
            self._state = new_state
            await asyncio.sleep(_BLOCKED_BACKOFF)
            return

        # Log every state observation at debug level
        logger.debug("[%s] state=%s (was=%s)", self.sku.sku, new_state.value, previous.value)

        # Only alert on genuine OOS -> IN_STOCK transition
        if new_state == StockState.IN_STOCK and previous != StockState.IN_STOCK:
            await self._fire_restock_alert(previous)

        self._state = new_state

    async def _fire_restock_alert(self, previous_state: StockState) -> None:
        logger.info("[%s] RESTOCK DETECTED (was %s)", self.sku.sku, previous_state.value)

        # Terminal alert — immediate, no network required
        print_terminal_alert(self.sku)

        # Browser launch — immediate
        if self.cfg.auto_open_browser:
            launch(self.sku)

        # Network notifications — concurrent, non-blocking
        try:
            await self.notifier.send_restock_alert(self.sku, previous_state.value)
        except Exception as exc:
            logger.error("[%s] Notification failed: %s", self.sku.sku, exc)

    def _next_delay(self) -> float:
        """
        Adaptive interval with jitter.

        Fast interval: product was OOS or unknown (restock could happen soon).
        Normal interval: product is in stock (monitoring for future OOS events).
        Blocked: handled inside _handle_state_change with its own sleep.
        """
        if self._state in (StockState.OUT_OF_STOCK, StockState.UNKNOWN):
            base = self.cfg.poll_interval_fast
        else:
            base = self.cfg.poll_interval

        jitter = random.uniform(0, self.cfg.poll_jitter_max)
        return base + jitter


class StockMonitor:
    """Manages multiple SKUMonitors and shared resources."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._fetcher: Fetcher | None = None
        self._notifier: Notifier | None = None

    async def run(self) -> None:
        active = self.cfg.active_skus()
        if not active:
            logger.error("No enabled SKUs in skus.json. Add products and set enabled=true.")
            return

        if not self.cfg.has_notifier:
            logger.warning(
                "No notification channels configured. "
                "Alerts will only appear in the terminal. "
                "Set DISCORD_WEBHOOK_URL or TELEGRAM_* in .env"
            )

        async with Fetcher(proxy=self.cfg.http_proxy) as fetcher:
            notifier = Notifier(self.cfg)
            try:
                monitors = [
                    SKUMonitor(sku, self.cfg, fetcher, notifier)
                    for sku in active
                ]

                logger.info(
                    "Monitoring %d SKU(s). Fast interval: %.0fs, Normal interval: %.0fs, Jitter: 0–%.0fs",
                    len(monitors),
                    self.cfg.poll_interval_fast,
                    self.cfg.poll_interval,
                    self.cfg.poll_jitter_max,
                )

                await asyncio.gather(*(m.run() for m in monitors))
            finally:
                await notifier.close()
