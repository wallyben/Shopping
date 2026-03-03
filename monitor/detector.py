"""
Stock detection logic for Pokemon Center UK product pages.
UPDATED with better anti-detection and human-like patterns.
"""

from __future__ import annotations

import json
import logging
import re
import random
import time
from enum import Enum
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class StockState(Enum):
    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UNKNOWN = "UNKNOWN"        # Parse failed — treat as inconclusive
    BLOCKED = "BLOCKED"        # Got a Cloudflare/bot challenge page


# Text patterns that appear on OOS pages (lowercase match)
_OOS_TEXT_PATTERNS = [
    "out of stock",
    "sold out",
    "currently unavailable",
    "notify me when available",
    "email me when available",
    "temporarily out of stock",
]

# Text patterns that confirm in-stock state
_IN_STOCK_TEXT_PATTERNS = [
    "add to cart",
    "add to bag",
    "pre-order now",
    "preorder now",
]

# JSON-LD availability values
_JSONLD_IN_STOCK = {
    "http://schema.org/instock",
    "https://schema.org/instock",
    "instock",
    "instock",
}
_JSONLD_OOS = {
    "http://schema.org/outofstock",
    "https://schema.org/outofstock",
    "outofstock",
    "limitedavailability",
    "http://schema.org/limitedavailability",
    "discontinued",
}


def detect(html: str, url: str) -> StockState:
    """
    Parse product page HTML and return current stock state.
    Includes human-like random delays to avoid detection patterns.
    """
    # Add small random delay before parsing (makes detection less predictable)
    time.sleep(random.uniform(0.1, 0.5))
    
    soup = BeautifulSoup(html, "lxml")

    # --- Bot challenge detection ---
    title = soup.title.string.lower() if soup.title and soup.title.string else ""
    if "just a moment" in title or "checking your browser" in title:
        logger.warning("🚫 Cloudflare challenge detected for %s", url)
        return StockState.BLOCKED
    
    # Check for other block indicators
    body_text = soup.get_text(separator=" ", limit=1000).lower()
    if "access denied" in body_text or "blocked" in body_text:
        logger.warning("🚫 Access blocked for %s", url)
        return StockState.BLOCKED

    # --- JSON-LD structured data (most reliable if present) ---
    state = _check_jsonld(soup, url)
    if state is not None:
        return state

    # --- HTML button / text analysis ---
    page_text = soup.get_text(separator=" ").lower()

    # Check for add-to-cart button that is NOT disabled
    add_to_cart_btn = soup.find(
        lambda tag: (
            tag.name in ("button", "a", "div")
            and any(p in (tag.get_text(strip=True) or "").lower() for p in _IN_STOCK_TEXT_PATTERNS)
            and not tag.has_attr("disabled")
            and "disabled" not in (tag.get("class", []))
            and tag.is_visible()  # Only count visible buttons
        )
    )
    if add_to_cart_btn:
        logger.debug("✅ Enabled add-to-cart button found for %s", url)
        return StockState.IN_STOCK

    # Disabled add-to-cart button → OOS signal
    disabled_btn = soup.find(
        lambda tag: (
            tag.name in ("button", "a", "div")
            and any(p in (tag.get_text(strip=True) or "").lower() for p in _IN_STOCK_TEXT_PATTERNS)
            and (tag.has_attr("disabled") or "disabled" in (tag.get("class", [])))
        )
    )
    if disabled_btn:
        logger.debug("❌ Disabled add-to-cart button found for %s", url)
        return StockState.OUT_OF_STOCK

    # OOS text patterns
    for pattern in _OOS_TEXT_PATTERNS:
        if pattern in page_text:
            logger.debug("📝 OOS text pattern '%s' found for %s", pattern, url)
            return StockState.OUT_OF_STOCK

    # In-stock text patterns (without button check)
    for pattern in _IN_STOCK_TEXT_PATTERNS:
        if pattern in page_text:
            logger.debug("📝 In-stock text pattern '%s' found for %s", pattern, url)
            return StockState.IN_STOCK

    # No product section at all — page may be incomplete or JS-rendered
    if not soup.find(class_=re.compile(r"product|pdp|item|details", re.I)):
        logger.warning(
            "⚠️ No product section found in HTML for %s — page may be JS-rendered or structure changed",
            url,
        )

    return StockState.UNKNOWN


def _check_jsonld(soup: BeautifulSoup, url: str) -> Optional[StockState]:
    """
    Extract schema.org/Product JSON-LD and return stock state if found.
    Returns None if no usable JSON-LD found.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        # Handle both single object and array
        items = data if isinstance(data, list) else [data]

        for item in items:
            # Recurse into @graph if present
            if "@graph" in item:
                items.extend(item["@graph"])
                continue

            # Handle different schema type formats
            schema_type = item.get("@type", "")
            if isinstance(schema_type, list):
                schema_type = schema_type[0] if schema_type else ""
                
            if schema_type not in ("Product", "http://schema.org/Product", "Product"):
                continue

            # Handle nested offers structure
            offers = item.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            
            # Handle offers as nested object with @type
            if isinstance(offers, dict) and offers.get("@type") in ("Offer", "http://schema.org/Offer"):
                availability = offers.get("availability", "").lower().replace(" ", "").replace("-", "")
            else:
                availability = offers.get("availability", "").lower().replace(" ", "").replace("-", "")

            if not availability:
                continue

            # Check against known patterns
            if any(pattern in availability for pattern in _JSONLD_IN_STOCK):
                logger.debug("📊 JSON-LD: in stock for %s", url)
                return StockState.IN_STOCK
            if any(pattern in availability for pattern in _JSONLD_OOS):
                logger.debug("📊 JSON-LD: out of stock for %s", url)
                return StockState.OUT_OF_STOCK

    return None
