

import requests
import random
import time
from fake_useragent import UserAgent
from typing import Optional

class Fetcher:
    def __init__(self, proxy: Optional[str] = None, jitter_max: float = 15):
        self.session = requests.Session()
        self.ua = UserAgent()
        self.proxy = proxy
        self.jitter_max = jitter_max
        
        # Set up proxy if provided
        if proxy:
            self.session.proxies = {
                'http': proxy,
                'https': proxy
            }
    
    def _get_headers(self) -> dict:
        """Generate realistic browser headers"""
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-GB,en;q=0.5',
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
    
    def fetch(self, url: str, add_jitter: bool = True):
        """Fetch URL with human-like timing and headers"""
        if add_jitter:
            # Random delay before request (0 to jitter_max seconds)
            jitter = random.uniform(0, self.jitter_max)
            time.sleep(jitter)
        
        headers = self._get_headers()
        
        try:
            response = self.session.get(
                url, 
                headers=headers, 
                timeout=10,
                allow_redirects=True
            )
            return response
        except requests.exceptions.RequestException as e:
            print(f"Fetch error: {e}")
            return None
    
    def close(self):
        """Close the session"""
        self.session.close()
