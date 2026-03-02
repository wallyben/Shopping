"""
Core monitoring loop.

Changes from original:
  - StockMonitor and SKUMonitor accept an optional asyncio.Queue (event_queue).
  - Every state change pushes a structured event dict to that queue.
  - The GUI controller reads from that queue and dispatches UI callbacks.
  - When no event_queue is provided behaviour is identical to the original CLI mode.

Event schema pushed to queue:
  {
    'type': 'state_change',
    'sku_url':    str,
    'sku_name':   str,
    'sku_id':     str,
    'old_state':  str,   # StockState.value
    'new_state':  str,
    'timestamp':  datetime,
    'is_restock': bool,  # True only on OOS/UNKNOWN -> IN_STOCK
  }
  {
    'type': 'log',
    'level':   str,   # 'INFO' | 'WARNING' | 'ERROR'
    'message': str,
    'timestamp': datetime,
  }
  {
    'type': 'poll_tick',   # emitted after every poll cycle
    'sku_url': str,
    'timestamp': datetime,
  }
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Optional

from .config import Config, SKU
from .detector import StockState, detect
from .fetcher import Fetcher
from .launcher import launch, print_terminal_alert
from .notifier import Notifier

logger = logging.getLogger(__name__)

_BLOCKED_BACKOFF = 300.0
_UNKNOWN_WARN_THRESHOLD = 5


class SKUMonitor:
    """Monitors a single SKU."""

    def __init__(
        self,
        sku: SKU,
        cfg: Config,
        fetcher: Fetcher,
        notifier: Notifier,
        event_queue: Optional[asyncio.Queue] = None,
    ):
        self.sku = sku
        self.cfg = cfg
        self.fetcher = fetcher
        self.notifier = notifier
        self._event_queue = event_queue

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
            await self._push_log("ERROR", f"[{self.sku.sku}] Fetch error: {exc}")
            return

        new_state = detect(html, self.sku.url)
        await self._handle_state_change(new_state)
        await self._push_event({
            'type': 'poll_tick',
            'sku_url': self.sku.url,
            'timestamp': datetime.now(),
        })

    async def _handle_state_change(self, new_state: StockState) -> None:
        previous = self._state

        if new_state == StockState.UNKNOWN:
            self._consecutive_unknown += 1
            if self._consecutive_unknown >= _UNKNOWN_WARN_THRESHOLD:
                msg = (
                    f"WARNING: {self.sku.display()} has returned UNKNOWN state "
                    f"{self._consecutive_unknown} times in a row. "
                    f"Site structure may have changed."
                )
                logger.warning(msg)
                await self.notifier.send_status_update(msg)
                await self._push_log("WARNING", msg)
                self._consecutive_unknown = 0
            self._state = new_state
            await self._push_state_event(previous, new_state, is_restock=False)
            return

        self._consecutive_unknown = 0

        if new_state == StockState.BLOCKED:
            logger.warning("[%s] Blocked — backing off %ds", self.sku.sku, _BLOCKED_BACKOFF)
            await self._push_log("WARNING", f"[{self.sku.sku}] Cloudflare blocked — backing off 5min")
            self._state = new_state
            await self._push_state_event(previous, new_state, is_restock=False)
            await asyncio.sleep(_BLOCKED_BACKOFF)
            return

        logger.debug("[%s] state=%s (was=%s)", self.sku.sku, new_state.value, previous.value)

        is_restock = new_state == StockState.IN_STOCK and previous != StockState.IN_STOCK
        await self._push_state_event(previous, new_state, is_restock=is_restock)

        if is_restock:
            await self._fire_restock_alert(previous)

        self._state = new_state

    async def _fire_restock_alert(self, previous_state: StockState) -> None:
        logger.info("[%s] RESTOCK DETECTED (was %s)", self.sku.sku, previous_state.value)

        print_terminal_alert(self.sku)

        if self.cfg.auto_open_browser:
            launch(self.sku)

        try:
            await self.notifier.send_restock_alert(self.sku, previous_state.value)
        except Exception as exc:
            logger.error("[%s] Notification failed: %s", self.sku.sku, exc)
            await self._push_log("ERROR", f"[{self.sku.sku}] Notification failed: {exc}")

    def mark_in_stock_from_category_signal(self) -> None:
        """
        Keep SKU monitor state in sync when category-path alerts are fired.
        This prevents the next SKU poll from emitting a duplicate restock alert.
        """
        self._state = StockState.IN_STOCK
        self._consecutive_unknown = 0

    def _next_delay(self) -> float:
        if self._state in (StockState.OUT_OF_STOCK, StockState.UNKNOWN):
            base = self.cfg.poll_interval_fast
        else:
            base = self.cfg.poll_interval
        return base + random.uniform(0, self.cfg.poll_jitter_max)

    async def _push_state_event(
        self, old: StockState, new: StockState, is_restock: bool
    ) -> None:
        await self._push_event({
            'type': 'state_change',
            'sku_url':    self.sku.url,
            'sku_name':   self.sku.name,
            'sku_id':     self.sku.sku,
            'old_state':  old.value,
            'new_state':  new.value,
            'timestamp':  datetime.now(),
            'is_restock': is_restock,
        })

    async def _push_log(self, level: str, message: str) -> None:
        await self._push_event({
            'type':      'log',
            'level':     level,
            'message':   message,
            'timestamp': datetime.now(),
        })

    async def _push_event(self, event: dict) -> None:
        if self._event_queue is not None:
            await self._event_queue.put(event)


class StockMonitor:
    """Manages multiple SKUMonitors and shared resources."""

    def __init__(self, cfg: Config, event_queue: Optional[asyncio.Queue] = None):
        self.cfg = cfg
        self._event_queue = event_queue
        self._monitors_by_sku: dict[str, SKUMonitor] = {}

    def mark_in_stock_from_category_signal(self, sku_id: str) -> None:
        monitor = self._monitors_by_sku.get(sku_id)
        if monitor is not None:
            monitor.mark_in_stock_from_category_signal()

    async def run(self) -> None:
        active = self.cfg.active_skus()
        if not active:
            logger.error("No enabled SKUs. Add products and enable them.")
            return

        if not self.cfg.has_notifier:
            logger.warning("No notification channels configured.")

        async with Fetcher(proxy=self.cfg.http_proxy) as fetcher:
            notifier = Notifier(self.cfg)
            try:
                monitors = [
                    SKUMonitor(sku, self.cfg, fetcher, notifier, self._event_queue)
                    for sku in active
                ]
                self._monitors_by_sku = {m.sku.sku: m for m in monitors}

                logger.info(
                    "Monitoring %d SKU(s). Fast: %.0fs  Normal: %.0fs  Jitter: 0–%.0fs",
                    len(monitors),
                    self.cfg.poll_interval_fast,
                    self.cfg.poll_interval,
                    self.cfg.poll_jitter_max,
                )

                await asyncio.gather(*(m.run() for m in monitors))
            finally:
                self._monitors_by_sku = {}
                await notifier.close()
