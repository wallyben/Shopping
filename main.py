#!/usr/bin/env python3
"""
Pokemon Center UK Restock Monitor
Entry point.

Usage:
    python main.py [--log-level DEBUG|INFO|WARNING]

Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def _setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, stream=sys.stdout)
    # Quieten noisy library loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)


def _check_env() -> None:
    env_file = Path(".env")
    if not env_file.exists():
        print(
            "WARNING: .env file not found.\n"
            "Copy .env.example to .env and fill in your settings.\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pokemon Center UK restock monitor"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    args = parser.parse_args()

    _check_env()
    _setup_logging(args.log_level)

    logger = logging.getLogger("main")

    from monitor.config import Config
    from monitor.stock_monitor import StockMonitor

    try:
        cfg = Config.load()
    except FileNotFoundError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)

    active = cfg.active_skus()
    if not active:
        logger.error(
            "No enabled SKUs found in skus.json.\n"
            "Edit skus.json, add your product URLs, and set \"enabled\": true."
        )
        sys.exit(1)

    logger.info("Pokemon Center UK Restock Monitor starting")
    logger.info("Monitoring %d product(s):", len(active))
    for sku in active:
        logger.info("  - %s", sku.display())

    monitor = StockMonitor(cfg)

    try:
        asyncio.run(monitor.run())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")


if __name__ == "__main__":
    main()
