"""
Category-page monitor for Pokemon Center UK.

Polls category pages on a ~30 s cadence to detect SKU availability changes
before committing a per-product confirmation fetch.

Flow
----
1. Every poll_interval (+jitter) both CATEGORY_URLS are fetched.
2. Product tiles are parsed; SKU, name, and availability are extracted.
3. For each monitored SKU we track in-memory last-known availability.
4. When a SKU transitions from OUT_OF_STOCK to anything else we emit a
   'category_signal' event to the shared asyncio.Queue.

Event pushed to queue
---------------------
{
    'type':      'category_signal',
    'sku':       str,        # SKU ID matching monitor.config.SKU.sku
    'name':      str,        # Product name from tile (may be empty string)
    'url':       str,        # Full product URL from config
    'timestamp': datetime,
}

The MonitorController handles 'category_signal' by doing a single
product-page confirmation fetch and, if confirmed (or if the page is
blocked), triggers the existing restock notification flow.

Parsing strategy
----------------
Category page tiles are found by three fallback strategies:
  1. Elements whose class name matches product-tile / product-card patterns.
  2. Any element carrying a known SKU data attribute.
  3. Anchor tags whose href contains /product/<sku>.

For each tile the SKU is extracted (data attribute → ancestor attribute →
href pattern), then name and availability are determined from button state
and text patterns — mirroring the logic in detector.py.

State management
----------------
In-memory only.  No DB reads/writes.  The dict is pre-seeded as empty so
only *changes* (not initial state) fire signals, eliminating false positives
on startup.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from .config import Config, SKU
from .fetcher import Fetcher

logger = logging.getLogger(__name__)

# ── Category URLs to poll ─────────────────────────────────────────────────────

CATEGORY_URLS: list[str] = [
    "https://www.pokemoncenter.com/en-gb/category/trading-card-game",
    "https://www.pokemoncenter.com/en-gb/category/new-releases",
]

# ── HTML parsing constants ─────────────────────────────────────────────────────

# Class-name patterns that identify product tile containers
_TILE_CLASS_RE = re.compile(
    r"product[_-]?(?:tile|card|grid)|grid[_-]?tile", re.I
)

# data-* attributes that may carry the SKU / product ID on a tile element
_SKU_ATTRS = (
    "data-sku",
    "data-product-id",
    "data-pid",
    "data-item-id",
    "data-variant-id",
)

# Regex to extract SKU from a product URL path: /en-gb/product/<sku>
_URL_SKU_RE = re.compile(r"/product/([^/?#]+)")

# Class-name patterns for product name elements within a tile
_NAME_CLASS_RE = re.compile(
    r"product[_-]?(?:name|title)|tile[_-]?(?:name|title)", re.I
)

# Text patterns that indicate an enabled add-to-cart control (in-stock)
_IN_STOCK_TEXT = ("add to cart", "add to bag")

# Text patterns that indicate the tile is out of stock
_OOS_TEXT = (
    "out of stock",
    "sold out",
    "notify me",
    "email me when available",
    "currently unavailable",
)


class CategoryMonitor:
    """
    Polls CATEGORY_URLS and emits 'category_signal' events when a monitored
    SKU appears to transition from OUT_OF_STOCK to a potentially in-stock
    state.

    Parameters
    ----------
    target_skus : list[SKU]
        The active SKUs to watch (from Config.active_skus()).
    cfg : Config
        Monitor configuration (used for poll_interval and poll_jitter_max).
    fetcher : Fetcher
        Shared HTTP client — the same Fetcher instance used elsewhere so we
        don't open a second connection pool.
    event_queue : asyncio.Queue
        Shared event queue consumed by MonitorController._event_reader.
    """

    def __init__(
        self,
        target_skus: list[SKU],
        cfg: Config,
        fetcher: Fetcher,
        event_queue: asyncio.Queue,
    ) -> None:
        # Index targets by sku string for O(1) membership checks
        self._targets: dict[str, SKU] = {s.sku: s for s in target_skus}
        self._cfg = cfg
        self._fetcher = fetcher
        self._event_queue = event_queue

        # In-memory availability state: sku_id -> "IN_STOCK"|"OUT_OF_STOCK"|"UNKNOWN"
        # Starts empty — only changes (not initial state) fire signals.
        self._state: dict[str, str] = {}

    # ── Public interface ───────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main polling loop.  Runs until the containing task is cancelled."""
        logger.info(
            "[CategoryMonitor] Starting — watching %d SKU(s) across %d category URL(s)",
            len(self._targets),
            len(CATEGORY_URLS),
        )
        while True:
            for url in CATEGORY_URLS:
                await self._poll(url)
            delay = self._cfg.poll_interval + random.uniform(
                0, self._cfg.poll_jitter_max
            )
            logger.debug("[CategoryMonitor] Next poll in %.1fs", delay)
            await asyncio.sleep(delay)

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _poll(self, category_url: str) -> None:
        """Fetch one category page and process matching tiles."""
        try:
            resp = await self._fetcher.get(category_url)
        except Exception as exc:
            logger.warning(
                "[CategoryMonitor] Fetch error for %s: %s", category_url, exc
            )
            return

        tiles = self._parse_tiles(resp.text, category_url)
        logger.debug(
            "[CategoryMonitor] %s — %d matching tile(s) found",
            category_url,
            len(tiles),
        )

        for tile in tiles:
            sku_id = tile["sku"]
            availability = tile["availability"]
            prev = self._state.get(sku_id)

            # Update in-memory state unconditionally
            self._state[sku_id] = availability

            # Fire signal only when we had a confirmed OOS baseline and now
            # the tile no longer shows OOS
            if prev == "OUT_OF_STOCK" and availability != "OUT_OF_STOCK":
                target = self._targets[sku_id]
                logger.info(
                    "[CategoryMonitor] SKU %s (%s): %s → %s — emitting category_signal",
                    sku_id,
                    target.name,
                    prev,
                    availability,
                )
                await self._event_queue.put({
                    "type":      "category_signal",
                    "sku":       sku_id,
                    "name":      tile.get("name") or target.name,
                    "url":       target.url,
                    "timestamp": datetime.now(),
                })

    # ── HTML parsing ──────────────────────────────────────────────────────────

    def _parse_tiles(self, html: str, category_url: str) -> list[dict]:
        """
        Extract product tile data from category-page HTML.

        Returns a list of dicts — one per tile whose SKU is in self._targets:
            {"sku": str, "name": str, "availability": str}

        Three discovery strategies are tried in order, stopping at the first
        that yields any elements.
        """
        soup = BeautifulSoup(html, "lxml")
        candidate_elements: list = []

        # Strategy 1: class-name pattern matching
        candidate_elements = soup.find_all(class_=_TILE_CLASS_RE)

        # Strategy 2: elements carrying known SKU data attributes
        if not candidate_elements:
            for attr in _SKU_ATTRS:
                candidate_elements = soup.find_all(attrs={attr: True})
                if candidate_elements:
                    break

        # Strategy 3: anchor tags whose href contains /product/<sku>
        if not candidate_elements:
            candidate_elements = soup.find_all("a", href=_URL_SKU_RE)

        results: list[dict] = []
        seen: set[str] = set()

        for element in candidate_elements:
            sku_id = self._extract_sku(element)
            if not sku_id or sku_id not in self._targets or sku_id in seen:
                continue
            seen.add(sku_id)

            results.append({
                "sku":          sku_id,
                "name":         self._extract_name(element),
                "availability": self._extract_availability(element),
            })

        if not results and self._targets:
            logger.debug(
                "[CategoryMonitor] No matching tiles found on %s "
                "(page may be JS-rendered or tile structure has changed)",
                category_url,
            )

        return results

    def _extract_sku(self, element) -> Optional[str]:
        """
        Extract SKU from element data attributes, ancestor attributes,
        or an anchor href.  Returns None if nothing is found.
        """
        # Direct attributes on the element
        for attr in _SKU_ATTRS:
            val = element.get(attr)
            if val:
                return str(val).strip()

        # Walk up to three ancestor levels
        ancestor = element.parent
        for _ in range(3):
            if ancestor is None:
                break
            for attr in _SKU_ATTRS:
                val = ancestor.get(attr)
                if val:
                    return str(val).strip()
            ancestor = ancestor.parent

        # Fall back to extracting from the nearest anchor href
        link = element if element.name == "a" else element.find("a", href=True)
        if link:
            m = _URL_SKU_RE.search(link.get("href", ""))
            if m:
                return m.group(1)

        return None

    def _extract_name(self, element) -> str:
        """Return product name from a tile element, or empty string."""
        name_el = element.find(class_=_NAME_CLASS_RE)
        if not name_el:
            name_el = element.find(["h2", "h3", "h4"])
        if name_el:
            return name_el.get_text(strip=True)
        return ""

    def _extract_availability(self, element) -> str:
        """
        Determine tile availability.

        Checks (in order):
          1. Enabled add-to-cart / add-to-bag button → IN_STOCK
          2. Disabled add-to-cart button             → OUT_OF_STOCK
          3. OOS text patterns                       → OUT_OF_STOCK
          4. In-stock text patterns                  → IN_STOCK
          5. Default                                 → UNKNOWN
        """
        tile_text = element.get_text(separator=" ").lower()

        # Enabled add-to-cart → in stock
        atc_enabled = element.find(
            lambda tag: (
                tag.name in ("button", "a")
                and any(
                    p in (tag.get_text(strip=True) or "").lower()
                    for p in _IN_STOCK_TEXT
                )
                and not tag.has_attr("disabled")
                and "disabled" not in (tag.get("class") or [])
            )
        )
        if atc_enabled:
            return "IN_STOCK"

        # Disabled add-to-cart → out of stock
        atc_disabled = element.find(
            lambda tag: (
                tag.name in ("button", "a")
                and any(
                    p in (tag.get_text(strip=True) or "").lower()
                    for p in _IN_STOCK_TEXT
                )
                and (
                    tag.has_attr("disabled")
                    or "disabled" in (tag.get("class") or [])
                )
            )
        )
        if atc_disabled:
            return "OUT_OF_STOCK"

        for pattern in _OOS_TEXT:
            if pattern in tile_text:
                return "OUT_OF_STOCK"

        for pattern in _IN_STOCK_TEXT:
            if pattern in tile_text:
                return "IN_STOCK"

        return "UNKNOWN"
