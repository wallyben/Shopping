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

from monitor.config import Config, SKU as MonitorSKU
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

        monitor = StockMonitor(config, event_queue=self._event_queue)

        monitor_task = asyncio.create_task(monitor.run())
        reader_task = asyncio.create_task(self._event_reader())

        await self._stop_event.wait()

        monitor_task.cancel()
        reader_task.cancel()

        await asyncio.gather(monitor_task, reader_task, return_exceptions=True)

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
