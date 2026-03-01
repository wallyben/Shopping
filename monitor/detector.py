"""
Stock detection logic for Pokemon Center UK product pages.

Strategy:
  1. Primary: parse HTML for Add to Cart button presence / disabled state.
  2. Secondary: look for JSON-LD structured data with availability field.
  3. Tertiary: check for known OOS text strings as a fallback signal.

Returns a StockState enum. Never raises — unknown states are returned as
UNKNOWN so the caller can decide whether to alert or retry.

Pokemon Center UK does not expose a public JSON/GraphQL API for stock status
as of the time of writing. If the site migrates to a client-rendered SPA that
hides stock behind XHR, this detector will break and must be replaced with
the Playwright-based fetcher. The monitor logs a WARNING when it detects
that the parsed page looks incomplete (no product section found).
"""

from __future__ import annotations

import json
import logging
import re
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
    "notify me",
    "email me when available",
]

# Text patterns that confirm in-stock state
_IN_STOCK_TEXT_PATTERNS = [
    "add to cart",
    "add to bag",
]

# JSON-LD availability values
_JSONLD_IN_STOCK = {
    "http://schema.org/instock",
    "https://schema.org/instock",
    "instock",
}
_JSONLD_OOS = {
    "http://schema.org/outofstock",
    "https://schema.org/outofstock",
    "outofstock",
    "http://schema.org/discontinued",
}


def detect(html: str, url: str) -> StockState:
    """
    Parse product page HTML and return current stock state.
    """
    soup = BeautifulSoup(html, "lxml")

    # --- Bot challenge detection ---
    title = soup.title.string.lower() if soup.title and soup.title.string else ""
    if "just a moment" in title or "checking your browser" in title:
        logger.warning("Cloudflare challenge detected for %s", url)
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
            tag.name in ("button", "a")
            and any(p in (tag.get_text(strip=True) or "").lower() for p in _IN_STOCK_TEXT_PATTERNS)
            and not tag.has_attr("disabled")
            and "disabled" not in (tag.get("class", []))
        )
    )
    if add_to_cart_btn:
        logger.debug("Add-to-cart button found (enabled) for %s", url)
        return StockState.IN_STOCK

    # Disabled add-to-cart button → OOS signal
    disabled_btn = soup.find(
        lambda tag: (
            tag.name in ("button", "a")
            and any(p in (tag.get_text(strip=True) or "").lower() for p in _IN_STOCK_TEXT_PATTERNS)
            and (tag.has_attr("disabled") or "disabled" in (tag.get("class", [])))
        )
    )
    if disabled_btn:
        logger.debug("Disabled add-to-cart button found for %s", url)
        return StockState.OUT_OF_STOCK

    # OOS text patterns
    for pattern in _OOS_TEXT_PATTERNS:
        if pattern in page_text:
            logger.debug("OOS text pattern '%s' found for %s", pattern, url)
            return StockState.OUT_OF_STOCK

    # In-stock text patterns (without button check)
    for pattern in _IN_STOCK_TEXT_PATTERNS:
        if pattern in page_text:
            logger.debug("In-stock text pattern '%s' found for %s", pattern, url)
            return StockState.IN_STOCK

    # No product section at all — page may be incomplete or JS-rendered
    if not soup.find(class_=re.compile(r"product|pdp|item", re.I)):
        logger.warning(
            "No product section found in HTML for %s — page may be JS-rendered or structure changed",
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

            schema_type = item.get("@type", "")
            if schema_type not in ("Product", "http://schema.org/Product"):
                continue

            offers = item.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}

            availability = offers.get("availability", "").lower().replace(" ", "")
            if not availability:
                continue

            if availability in _JSONLD_IN_STOCK:
                logger.debug("JSON-LD: in stock for %s", url)
                return StockState.IN_STOCK
            if availability in _JSONLD_OOS:
                logger.debug("JSON-LD: out of stock for %s", url)
                return StockState.OUT_OF_STOCK

    return None
