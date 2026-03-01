"""
HTTP client for fetching Pokemon Center UK product pages.

Design decisions:
- httpx with HTTP/2: matches modern browser behaviour more closely than requests.
- Headers mimic a real Chrome browser on Windows to reduce fingerprint distance.
- No JS execution: Pokemon Center product pages render stock status in HTML.
  If this assumption breaks (stock moved to client-side JS only), switch to
  the Playwright-based fetcher in fetcher_browser.py.
- Proxy support: optional, set HTTP_PROXY in .env.
- Retries: 3 attempts with exponential backoff on transient errors (5xx, timeout).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Headers that match Chrome 124 on Windows 10.
# Keeping Accept-Language as en-GB signals locale correctly for the en-gb subdomain.
_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0   # seconds
_REQUEST_TIMEOUT = 15.0   # seconds


class Fetcher:
    def __init__(self, proxy: Optional[str] = None):
        transport = httpx.AsyncHTTPTransport(retries=0, http2=True)
        self._client = httpx.AsyncClient(
            headers=_BASE_HEADERS,
            follow_redirects=True,
            timeout=_REQUEST_TIMEOUT,
            transport=transport,
            proxies={"all://": proxy} if proxy else None,
        )

    async def get(self, url: str) -> httpx.Response:
        """
        Fetch url with retry logic.
        Retries on: connection errors, timeouts, 429, 5xx.
        Raises on: 403 (likely blocked), 404, other 4xx.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.get(url)

                if resp.status_code == 200:
                    # --- DIAGNOSTIC ---
                    _body = resp.text
                    _jsonld_present = 'application/ld+json' in _body
                    logger.info(
                        "[DIAG] %s | status=%d | json-ld=%s | body[:800]=%r",
                        url, resp.status_code, _jsonld_present, _body[:800],
                    )
                    # --- END DIAGNOSTIC ---
                    return resp

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", _RETRY_BASE_DELAY * (2 ** attempt)))
                    logger.warning("429 rate limited on %s — waiting %.1fs", url, retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status_code >= 500:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning("HTTP %d from %s — retry in %.1fs", resp.status_code, url, delay)
                    await asyncio.sleep(delay)
                    continue

                # 403, 404, other 4xx — non-retryable
                logger.error("HTTP %d from %s — not retrying", resp.status_code, url)
                resp.raise_for_status()

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                last_exc = exc
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("Network error on %s (%s) — retry in %.1fs", url, exc, delay)
                await asyncio.sleep(delay)

        raise RuntimeError(
            f"All {_MAX_RETRIES} attempts failed for {url}"
        ) from last_exc

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
