#!/usr/bin/env python3
"""
Pokemon Center UK Restock Monitor — GUI entry point.

Double-click this file (or the compiled .exe) to launch the desktop app.
No terminal interaction required.
"""

from __future__ import annotations

import logging
import queue
import sys
from pathlib import Path

# Ensure project root is on sys.path when running as a script or frozen exe
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    # --- 1. Initialise database (auto-creates schema on first run) ---
    from app.storage.database import init_db
    init_db()

    # --- 2. Set up logging with a UI queue ---
    ui_log_queue: queue.Queue = queue.Queue(maxsize=2000)
    from app.utils.logging_setup import setup_logging
    setup_logging(ui_log_queue, level=logging.INFO)

    logger = logging.getLogger("main")
    logger.info("Pokemon Center UK Monitor starting")

    # --- 3. Launch GUI ---
    from app.ui.main_window import MainWindow
    app = MainWindow(ui_log_queue=ui_log_queue)
    app.mainloop()

    logger.info("Application closed")


if __name__ == "__main__":
    main()
