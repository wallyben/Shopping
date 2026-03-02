"""
MonitorController — bridges the async monitor engine and the CTk UI thread.

Threading model:
  - asyncio event loop runs in a single daemon thread ("monitor-thread").
  - The UI runs on the main thread.
  - Events flow: async monitor → asyncio.Queue → _event_reader coroutine
    → root.after(0, callback) → main thread UI update.
  - All UI callbacks are dispatched via root.after(0, ...).  CTk/Tk widgets
    must only be accessed from the thread that created them (main thread).
  - stop() calls loop.call_soon_threadsafe(stop_event.set) to signal the
    background loop without blocking the UI thread, then joins the thread
    with a 3-second timeout for graceful shutdown.

Failure isolation:
  - If the monitor thread crashes, the controller marks itself stopped and
    invokes on_error(message) so the UI can display the error state.
  - The UI is never left in "Running" state after a crash.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from monitor.category_monitor import CategoryMonitor
from monitor.config import Config, SKU as MonitorSKU
from monitor.detector import StockState, detect
from monitor.fetcher import Fetcher
from monitor.launcher import launch, print_terminal_alert
from monitor.notifier import Notifier
from monitor.stock_monitor import StockMonitor
from app.storage import database as db
from app.services import keyring_service as ks

logger = logging.getLogger(__name__)


def build_config_from_db() -> Config:
    """
    Construct a monitor.Config from the DB + keyring.
    Called on the main thread before handing off to the monitor thread.
    """
    products = db.get_enabled_products()
    settings = db.load_settings()

    skus = [
        MonitorSKU(
            name=p.name,
            url=p.url,
            sku=p.sku or p.url.split("/")[-1],
            enabled=True,
        )
        for p in products
    ]

    return Config(
        discord_webhook_url=ks.get_discord_webhook(),
        telegram_bot_token=ks.get_telegram_token(),
        telegram_chat_id=ks.get_telegram_chat_id(),
        poll_interval=settings.poll_interval,
        poll_interval_fast=settings.poll_interval_fast,
        poll_jitter_max=settings.poll_jitter_max,
        auto_open_browser=settings.auto_open_browser,
        http_proxy=settings.http_proxy or None,
        skus=skus,
    )


class MonitorController:
    """
    Lifecycle: start() → [monitoring] → stop()
    Safe to call start()/stop() multiple times.
    """

    def __init__(
        self,
        root,                          # CTk root window (for root.after)
        on_state_change: Callable,     # (sku_url, old_state, new_state, ts, is_restock)
        on_poll_tick: Callable,        # (sku_url, ts)
        on_error: Callable,            # (message: str)
    ):
        self._root = root
        self._on_state_change = on_state_change
        self._on_poll_tick = on_poll_tick
        self._on_error = on_error

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._event_queue: Optional[asyncio.Queue] = None
        self._running = False

        # Category-signal confirmation resources (set in _async_main)
        self._cat_fetcher: Optional[Fetcher] = None
        self._cat_notifier: Optional[Notifier] = None
        self._cat_config: Optional[Config] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, config: Config) -> None:
        if self._running:
            logger.warning("MonitorController.start() called while already running")
            return

        if not config.active_skus():
            self._on_error("No enabled products to monitor. Add and enable at least one product.")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._thread_main,
            args=(config,),
            daemon=True,
            name="monitor-thread",
        )
        self._thread.start()
        logger.info("Monitor controller started (%d SKUs)", len(config.active_skus()))

    def stop(self) -> None:
        if not self._running:
            return

        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

        if self._thread:
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("Monitor thread did not stop cleanly within 3s")

        self._running = False
        logger.info("Monitor controller stopped")

    # ------------------------------------------------------------------
    # Internal — runs on the monitor thread
    # ------------------------------------------------------------------

    def _thread_main(self, config: Config) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main(config))
        except Exception as exc:
            logger.error("Monitor thread fatal error: %s", exc, exc_info=True)
            self._root.after(0, lambda: self._on_error(f"Monitor crashed: {exc}"))
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._running = False

    async def _async_main(self, config: Config) -> None:
        self._stop_event = asyncio.Event()
        self._event_queue = asyncio.Queue()
        self._cat_config = config

        monitor = StockMonitor(config, event_queue=self._event_queue)

        # Separate Fetcher + Notifier for category polling and one-shot
        # product confirmation.  StockMonitor owns its own Fetcher internally.
        async with Fetcher(proxy=config.http_proxy) as cat_fetcher:
            self._cat_fetcher = cat_fetcher
            self._cat_notifier = Notifier(config)
            try:
                cat_monitor = CategoryMonitor(
                    config.active_skus(), config, cat_fetcher, self._event_queue
                )

                monitor_task = asyncio.create_task(monitor.run())
                cat_task = asyncio.create_task(cat_monitor.run())
                reader_task = asyncio.create_task(self._event_reader())

                await self._stop_event.wait()

                monitor_task.cancel()
                cat_task.cancel()
                reader_task.cancel()

                await asyncio.gather(
                    monitor_task, cat_task, reader_task, return_exceptions=True
                )
            finally:
                await self._cat_notifier.close()
                self._cat_fetcher = None
                self._cat_notifier = None

    async def _event_reader(self) -> None:
        """
        Drains the event queue and dispatches each event to the UI thread.
        Uses a short timeout so cancellation is responsive.
        """
        while True:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(), timeout=0.2
                )
            except asyncio.TimeoutError:
                continue

            if event.get("type") == "category_signal":
                # Handle entirely on the monitor thread — needs async fetch
                asyncio.create_task(self._handle_category_signal(event))
                continue

            # Capture event in closure to avoid late-binding bug
            self._root.after(0, lambda e=event: self._dispatch(e))

    def _dispatch(self, event: dict) -> None:
        """Called on the main thread via root.after(0, ...)."""
        etype = event.get("type")

        if etype == "state_change":
            # Update DB status — called from main thread, safe
            try:
                db.update_product_status(event["sku_url"], event["new_state"])
            except Exception as exc:
                logger.error("DB update_product_status failed: %s", exc)

            self._on_state_change(
                event["sku_url"],
                event["old_state"],
                event["new_state"],
                event["timestamp"],
                event["is_restock"],
            )

        elif etype == "poll_tick":
            self._on_poll_tick(event["sku_url"], event["timestamp"])

    # ------------------------------------------------------------------
    # Category-signal confirmation — runs on the monitor thread (async)
    # ------------------------------------------------------------------

    async def _handle_category_signal(self, event: dict) -> None:
        """
        One-shot product-page confirmation triggered by a category_signal.

        Steps
        -----
        1. Fetch the single product page once via the category Fetcher.
        2. Run the existing detector on the returned HTML.
        3. IN_STOCK          → trigger restock alert + emit state_change.
        4. UNKNOWN / BLOCKED → fail-open (category signal is strong) — same.
        5. OUT_OF_STOCK      → false alarm, log and return without alerting.
        """
        if self._cat_fetcher is None or self._cat_notifier is None or self._cat_config is None:
            return

        sku_id   = event["sku"]
        sku_url  = event["url"]
        sku_name = event["name"]

        sku_obj = next(
            (s for s in self._cat_config.active_skus() if s.sku == sku_id),
            None,
        )
        if sku_obj is None:
            logger.warning("[CategorySignal] SKU %s not in active config — skipping", sku_id)
            return

        logger.info(
            "[CategorySignal] Confirming %s (%s) via product page", sku_name, sku_id
        )

        # ── Single product-page fetch ──────────────────────────────────────
        confirmed_state = StockState.UNKNOWN
        try:
            resp = await self._cat_fetcher.get(sku_url)
            confirmed_state = detect(resp.text, sku_url)
            logger.info(
                "[CategorySignal] Product page result for %s: %s",
                sku_id,
                confirmed_state.value,
            )
        except Exception as exc:
            logger.warning(
                "[CategorySignal] Confirmation fetch failed for %s: %s — treating as UNKNOWN",
                sku_id,
                exc,
            )
            confirmed_state = StockState.UNKNOWN

        # ── Decision ──────────────────────────────────────────────────────
        if confirmed_state == StockState.IN_STOCK:
            reason = "product page confirmed IN_STOCK"
        elif confirmed_state in (StockState.UNKNOWN, StockState.BLOCKED):
            # Fail-open: category signal is strong, product page inconclusive
            reason = (
                f"fail-open — product page returned {confirmed_state.value}, "
                "category signal strong"
            )
        else:
            # OUT_OF_STOCK — category signal was a false positive
            logger.info(
                "[CategorySignal] %s (%s) still OOS on product page — no alert",
                sku_name,
                sku_id,
            )
            return

        # ── Fire restock alert ─────────────────────────────────────────────
        logger.info(
            "[CategorySignal] RESTOCK: %s (%s) — %s", sku_name, sku_id, reason
        )

        print_terminal_alert(sku_obj)

        if self._cat_config.auto_open_browser:
            launch(sku_obj)

        try:
            await self._cat_notifier.send_restock_alert(
                sku_obj, StockState.OUT_OF_STOCK.value
            )
        except Exception as exc:
            logger.error(
                "[CategorySignal] Notification failed for %s: %s", sku_id, exc
            )

        # Emit state_change so _dispatch handles DB update + UI callback
        if self._event_queue is not None:
            await self._event_queue.put({
                "type":      "state_change",
                "sku_url":   sku_url,
                "sku_name":  sku_name,
                "sku_id":    sku_id,
                "old_state": StockState.OUT_OF_STOCK.value,
                "new_state": StockState.IN_STOCK.value,
                "timestamp": datetime.now(),
                "is_restock": True,
            })
