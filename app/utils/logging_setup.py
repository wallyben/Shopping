"""
Logging configuration.

Two outputs:
  1. Rolling file log  →  logs/monitor.log  (max 5MB × 3 backups)
  2. UI queue handler  →  pushes records to a thread-safe queue that the
                          main window drains via root.after() polling.

Secrets are never passed to loggers in this codebase. The redaction filter
exists as a defence-in-depth guard against accidental future leaks.
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
import re
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "monitor.log"

_SECRET_PATTERN = re.compile(
    r"(discord_webhook|telegram|token|password|secret|proxy)[^\s]*\s*[:=]\s*\S+",
    re.IGNORECASE,
)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _SECRET_PATTERN.sub(r"\1: [REDACTED]", str(record.msg))
        return True


class UIQueueHandler(logging.Handler):
    """Pushes formatted log records into a thread-safe queue for the UI."""

    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self._queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait({
                'level':   record.levelname,
                'message': self.format(record),
            })
        except queue.Full:
            pass  # UI queue full — drop the record rather than block


def setup_logging(ui_queue: queue.Queue, level: int = logging.INFO) -> None:
    """
    Call once at application startup.
    ui_queue: thread-safe queue.Queue — the main window polls this.
    """
    LOG_DIR.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    redact = _RedactFilter()

    # --- Rolling file handler ---
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.addFilter(redact)
    root.addHandler(fh)

    # --- UI queue handler ---
    ui_fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    qh = UIQueueHandler(ui_queue)
    qh.setFormatter(ui_fmt)
    qh.addFilter(redact)
    root.addHandler(qh)

    # Quiet noisy third-party loggers
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
