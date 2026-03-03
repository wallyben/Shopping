"""
HTTP fetcher with session persistence and human-like headers.
"""

import asyncio
import random
import logging
from typing import Optional, Dict
from fake_useragent import UserAgent

import aiohttp

logger = logging.getLogger(__name__)


class Fetcher:
    """HTTP client with cookie persistence and realistic browser headers."""

    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy
        self.ua = UserAgent()
        self.session: Optional[aiohttp.ClientSession] = None
        self._last_headers: Dict[str, str] = {}
        
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a session with cookie persistence."""
        if self.session is None or self.session.closed:
            # Create session with cookie jar for persistence
            jar = aiohttp.CookieJar(unsafe=True)
            connector = aiohttp.TCPConnector(ssl=False, force_close=False)
            
            self.session = aiohttp.ClientSession(
                cookie_jar=jar,
                connector=connector,
                headers=self._generate_headers()
            )
        return self.session

    def _generate_headers(self) -> Dict[str, str]:
        """Generate realistic browser headers that change occasionally."""
        # 80% chance to keep same headers, 20% chance to rotate
        if self._last_headers and random.random() < 0.8:
            return self._last_headers
            
        headers = {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': random.choice([
                'en-GB,en;q=0.9',
                'en-US,en;q=0.9',
                'en-GB,en;q=0.8,fr;q=0.6'
            ]),
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        }
        
        # Add random headers occasionally
        if random.random() < 0.3:
            headers['X-Requested-With'] = 'XMLHttpRequest'
            
        self._last_headers = headers
        return headers

    async def get(self, url: str, timeout: int = 10) -> aiohttp.ClientResponse:
        """Fetch a URL with proper headers and timeout."""
        session = await self._get_session()
        
        # Add random delay before request (0.5-3 seconds)
        await asyncio.sleep(random.uniform(0.5, 3))
        
        try:
            response = await session.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                ssl=False
            )
            
            # Check if we got blocked
            if response.status in [403, 429, 503]:
                logger.warning(f"Got status {response.status} for {url}")
                # Add longer delay if blocked
                await asyncio.sleep(random.uniform(30, 60))
                
            return response
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout fetching {url}")
            raise
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            raise

    async def close(self):
        """Close the session."""
        if self.session and not self.session.closed:
            await self.session.close()
