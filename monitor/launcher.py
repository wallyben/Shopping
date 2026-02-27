"""
Quick-launch helper.

On restock detection, opens the product URL in the default system browser
as fast as possible so the user can complete checkout manually without
copy-pasting a URL.

Also prints a large, hard-to-miss alert to the terminal.
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser
import logging

from .config import SKU

logger = logging.getLogger(__name__)


def launch(sku: SKU) -> None:
    """
    Open product URL in default browser immediately.
    Uses webbrowser module as primary; falls back to subprocess open/xdg-open.
    """
    url = sku.url
    opened = False

    try:
        opened = webbrowser.open(url, new=2, autoraise=True)
    except Exception as exc:
        logger.debug("webbrowser.open failed: %s", exc)

    if not opened:
        # Fallback per platform
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", url])
            elif sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", url])
            elif sys.platform == "win32":
                subprocess.Popen(["start", url], shell=True)
            opened = True
        except Exception as exc:
            logger.error("Browser launch fallback failed: %s", exc)

    if opened:
        logger.info("Browser opened for %s", url)
    else:
        logger.error("Could not open browser — navigate manually: %s", url)


def print_terminal_alert(sku: SKU) -> None:
    border = "=" * 70
    print(f"\n{border}")
    print(f"  *** RESTOCK DETECTED ***")
    print(f"  Product : {sku.name}")
    print(f"  SKU     : {sku.sku}")
    print(f"  URL     : {sku.url}")
    print(f"{border}\n")
    sys.stdout.flush()
