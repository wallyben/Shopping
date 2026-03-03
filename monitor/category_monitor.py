"""
Category-page monitor for Pokemon Center UK.
UPDATED with anti-detection measures: longer intervals, random jitter, and human-like patterns.
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

# ── HTML parsing constants (unchanged) ────────────────────────────────────────
_TILE_CLASS_RE = re.compile(r"product[_-]?(?:tile|card|grid)|grid[_-]?tile", re.I)
_SKU_ATTRS = ("data-sku", "data-product-id", "data-pid", "data-item-id", "data-variant-id")
_URL_SKU_RE = re.compile(r"/product/([^/?#]+)")
_NAME_CLASS_RE = re.compile(r"product[_-]?(?:name|title)|tile[_-]?(?:name|title)", re.I)
_IN_STOCK_TEXT = ("add to cart", "add to bag")
_OOS_TEXT = ("out of stock", "sold out", "notify me", "email me when available", "currently unavailable")


class CategoryMonitor:
    """
    Polls CATEGORY_URLS and emits 'category_signal' events.
    UPDATED: Now includes human-like delays and random jitter to avoid detection.
    """

    def __init__(
        self,
        target_skus: list[SKU],
        cfg: Config,
        fetcher: Fetcher,
        event_queue: asyncio.Queue,
    ) -> None:
        self._targets: dict[str, SKU] = {s.sku: s for s in target_skus}
        self._cfg = cfg
        self._fetcher = fetcher
        self._event_queue = event_queue
        self._state: dict[str, str] = {}
        
        # Track request timing to avoid patterns
        self._last_request_time = 0
        self._request_count = 0

    async def _human_delay(self, min_seconds: float = 1, max_seconds: float = 3):
        """Add random delay with variable pattern to mimic human behavior."""
        # Base delay
        delay = random.uniform(min_seconds, max_seconds)
        
        # Occasionally add longer "thinking" delays
        if random.random() < 0.3:  # 30% chance
            delay += random.uniform(2, 5)
            
        await asyncio.sleep(delay)

    async def _respect_rate_limit(self):
        """Ensure we don't request too quickly."""
        now = datetime.now().timestamp()
        time_since_last = now - self._last_request_time
        
        # Minimum 2 seconds between requests
        if time_since_last < 2:
            await asyncio.sleep(2 - time_since_last)
        
        # Reset counter if we've been waiting a while
        if time_since_last > 30:
            self._request_count = 0
        
        self._last_request_time = datetime.now().timestamp()
        self._request_count += 1
        
        # If we've made many requests, take a longer break
        if self._request_count > 5:
            long_break = random.uniform(10, 20)
            logger.debug(f"Taking a {long_break:.1f}s break after {self._request_count} requests")
            await asyncio.sleep(long_break)
            self._request_count = 0

    async def run(self) -> None:
        """Main polling loop with anti-detection measures."""
        logger.info(
            "[CategoryMonitor] Starting — watching %d SKU(s) across %d category URL(s)",
            len(self._targets),
            len(CATEGORY_URLS),
        )
        
        while True:
            for url in CATEGORY_URLS:
                # Add human-like delay BEFORE each request
                await self._human_delay(2, 5)
                await self._respect_rate_limit()
                await self._poll(url)
            
            # Much longer delay between full cycles (5-10 minutes)
            cycle_delay = random.uniform(300, 600)  # 5-10 minutes
            logger.debug("[CategoryMonitor] Next full cycle in %.1f minutes", cycle_delay/60)
            await asyncio.sleep(cycle_delay)

    async def _poll(self, category_url: str) -> None:
        """Fetch one category page and process matching tiles."""
        try:
            # Add jitter before fetch
            await self._human_delay(1, 4)
            
            resp = await self._fetcher.get(category_url)
            
            # Check if we got blocked
            if resp.status_code in [403, 429, 503]:
                logger.warning(f"[CategoryMonitor] Got status {resp.status_code} -可能 blocked")
                # Take a long break if blocked
                await asyncio.sleep(random.uniform(300, 600))
                return
                
        except Exception as exc:
            logger.warning("[CategoryMonitor] Fetch error for %s: %s", category_url, exc)
            return

        tiles = self._parse_tiles(resp.text, category_url)
        
        if tiles:
            logger.debug("[CategoryMonitor] %s — %d matching tile(s) found", category_url, len(tiles))

        for tile in tiles:
            sku_id = tile["sku"]
            availability = tile["availability"]
            prev = self._state.get(sku_id)

            # Update in-memory state
            self._state[sku_id] = availability

            # Fire signal only on OOS → something else transition
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
                    "type": "category_signal",
                    "sku": sku_id,
                    "name": tile.get("name") or target.name,
                    "url": target.url,
                    "timestamp": datetime.now(),
                })

    # ── HTML parsing methods (unchanged from your original) ───────────────────
    def _parse_tiles(self, html: str, category_url: str) -> list[dict]:
        """Extract product tile data from category-page HTML."""
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
                "sku": sku_id,
                "name": self._extract_name(element),
                "availability": self._extract_availability(element),
            })

        return results

    def _extract_sku(self, element) -> Optional[str]:
        """Extract SKU from element."""
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
        """Return product name from a tile element."""
        name_el = element.find(class_=_NAME_CLASS_RE)
        if not name_el:
            name_el = element.find(["h2", "h3", "h4"])
        if name_el:
            return name_el.get_text(strip=True)
        return ""

    def _extract_availability(self, element) -> str:
        """Determine tile availability."""
        tile_text = element.get_text(separator=" ").lower()

        # Enabled add-to-cart → in stock
        atc_enabled = element.find(
            lambda tag: (
                tag.name in ("button", "a")
                and any(p in (tag.get_text(strip=True) or "").lower() for p in _IN_STOCK_TEXT)
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
                and any(p in (tag.get_text(strip=True) or "").lower() for p in _IN_STOCK_TEXT)
                and (tag.has_attr("disabled") or "disabled" in (tag.get("class") or []))
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
