"""
MainWindow — root CustomTkinter window.

Layout (top to bottom):
  toolbar     Add / Remove / Start / Stop / Settings / Test Alert buttons
  table       ProductTable (expands to fill space)
  log_panel   CTkTextbox (fixed 160px, scrollable, colour-coded)
  status_bar  Engine state | product count | last poll time

Thread safety:
  All UI mutations happen on the main thread.
  MonitorController dispatches events via root.after(0, ...) so no locking
  is needed inside this class.

First-run detection:
  If no products exist AND no notification channel is configured, a welcome
  prompt is shown guiding the user to Settings then Add Product.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from datetime import datetime
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from app.controller.monitor_controller import MonitorController, build_config_from_db
from app.storage import database as db
from app.ui.add_product_dialog import AddProductDialog
from app.ui.product_table import ProductTable
from app.ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Colour constants
_RED    = "#f44336"
_GREEN  = "#4caf50"
_ORANGE = "#ff9800"
_GREY   = "#9e9e9e"
_BG     = "#2b2b2b"

# How often (ms) the main thread polls the UI log queue
_LOG_POLL_MS = 100
# Max lines kept in the log panel before trimming
_LOG_MAX_LINES = 500


class MainWindow(ctk.CTk):
    def __init__(self, ui_log_queue: queue.Queue):
        super().__init__()

        self._log_queue = ui_log_queue
        self._controller: Optional[MonitorController] = None
        self._last_poll_ts: Optional[datetime] = None

        self.title("Pokemon Center UK Monitor")
        self.geometry("1100x720")
        self.minsize(800, 560)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_toolbar()
        self._build_table()
        self._build_log_panel()
        self._build_status_bar()

        self._load_products()
        self._start_log_poll()
        self._check_first_run()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = ctk.CTkFrame(self, height=52, corner_radius=0)
        tb.pack(fill="x", padx=0, pady=0)
        tb.pack_propagate(False)

        # Left group
        ctk.CTkButton(tb, text="+ Add Product",  width=130, command=self._add_product).pack(side="left", padx=(12, 4), pady=10)
        self._btn_remove = ctk.CTkButton(tb, text="Remove",  width=90, fg_color="#555", command=self._remove_product)
        self._btn_remove.pack(side="left", padx=4, pady=10)

        # Right group
        ctk.CTkButton(tb, text="Settings",      width=100, fg_color="#444", command=self._open_settings).pack(side="right", padx=(4, 12), pady=10)
        ctk.CTkButton(tb, text="Test Alert",    width=100, fg_color="#444", command=self._test_notification).pack(side="right", padx=4, pady=10)
        self._btn_stop  = ctk.CTkButton(tb, text="Stop",   width=90, fg_color="#8b1a1a", command=self._stop_monitor, state="disabled")
        self._btn_stop.pack(side="right", padx=4, pady=10)
        self._btn_start = ctk.CTkButton(tb, text="▶ Start", width=110, command=self._start_monitor)
        self._btn_start.pack(side="right", padx=4, pady=10)

    def _build_table(self) -> None:
        self._table = ProductTable(self, on_toggle_enabled=self._on_toggle_enabled)
        self._table.pack(fill="both", expand=True, padx=12, pady=(8, 0))

    def _build_log_panel(self) -> None:
        log_frame = ctk.CTkFrame(self, fg_color=_BG, corner_radius=6, height=170)
        log_frame.pack(fill="x", padx=12, pady=(8, 0))
        log_frame.pack_propagate(False)

        header = ctk.CTkFrame(log_frame, fg_color="#1a1a1a", height=24, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="  Log", anchor="w", font=ctk.CTkFont(size=11), text_color=_GREY).pack(side="left")
        ctk.CTkButton(header, text="Clear", width=48, height=20, fg_color="transparent", text_color=_GREY,
                      font=ctk.CTkFont(size=10), hover_color="#333", command=self._clear_log).pack(side="right", padx=4)

        self._log_box = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Courier New", size=10),
            fg_color=_BG,
            corner_radius=0,
            wrap="none",
            state="disabled",
        )
        self._log_box.pack(fill="both", expand=True)

        # Colour tags — CTkTextbox wraps tk.Text so tag_config works
        self._log_box._textbox.tag_configure("ERROR",   foreground=_RED)
        self._log_box._textbox.tag_configure("WARNING", foreground=_ORANGE)
        self._log_box._textbox.tag_configure("INFO",    foreground="#dce4ee")
        self._log_box._textbox.tag_configure("DEBUG",   foreground=_GREY)

    def _build_status_bar(self) -> None:
        sb = ctk.CTkFrame(self, height=28, corner_radius=0, fg_color="#1a1a1a")
        sb.pack(fill="x", padx=0, pady=(4, 0), side="bottom")
        sb.pack_propagate(False)

        self._lbl_engine = ctk.CTkLabel(sb, text="● Stopped", text_color=_RED,
                                        font=ctk.CTkFont(size=11), anchor="w")
        self._lbl_engine.pack(side="left", padx=12)

        ctk.CTkFrame(sb, width=1, fg_color="#444").pack(side="left", fill="y", padx=4, pady=4)

        self._lbl_count = ctk.CTkLabel(sb, text="0 products", text_color=_GREY,
                                       font=ctk.CTkFont(size=11), anchor="w")
        self._lbl_count.pack(side="left", padx=8)

        self._lbl_last = ctk.CTkLabel(sb, text="Last poll: —", text_color=_GREY,
                                      font=ctk.CTkFont(size=11), anchor="e")
        self._lbl_last.pack(side="right", padx=12)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_products(self) -> None:
        products = db.get_all_products()
        self._table.load_products(products)
        self._lbl_count.configure(text=f"{len(products)} product{'s' if len(products) != 1 else ''}")

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------

    def _add_product(self) -> None:
        dlg = AddProductDialog(self)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        try:
            product = db.add_product(
                name=dlg.result["name"],
                url=dlg.result["url"],
                sku=dlg.result["sku"],
            )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                messagebox.showerror("Duplicate URL", "That product URL is already being monitored.", parent=self)
            else:
                messagebox.showerror("Error", f"Could not add product:\n{exc}", parent=self)
            return
        self._table.add_product(product)
        self._refresh_count()
        logger.info("Product added: %s", product.name)

    def _remove_product(self) -> None:
        product_id = self._table.selected_product_id()
        if product_id is None:
            messagebox.showwarning("No Selection", "Select a product to remove.", parent=self)
            return
        if not messagebox.askyesno("Confirm Remove", "Remove selected product?", parent=self):
            return
        db.delete_product(product_id)
        self._table.remove_product(product_id)
        self._refresh_count()
        logger.info("Product removed (id=%d)", product_id)

    def _start_monitor(self) -> None:
        if self._controller and self._controller.is_running:
            return

        config = build_config_from_db()
        if not config.active_skus():
            messagebox.showwarning(
                "No Products",
                "Add at least one enabled product before starting.",
                parent=self,
            )
            return

        self._controller = MonitorController(
            root=self,
            on_state_change=self._on_state_change,
            on_poll_tick=self._on_poll_tick,
            on_error=self._on_monitor_error,
        )
        self._controller.start(config)

        self._btn_start.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._lbl_engine.configure(text="● Running", text_color=_GREEN)
        logger.info("Monitoring started")

    def _stop_monitor(self) -> None:
        if self._controller:
            self._controller.stop()
            self._controller = None

        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        self._lbl_engine.configure(text="● Stopped", text_color=_RED)
        self._lbl_last.configure(text="Last poll: —")
        logger.info("Monitoring stopped")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        self.wait_window(dlg)

    def _test_notification(self) -> None:
        dlg = SettingsDialog(self)
        self.wait_window(dlg)

    def _on_toggle_enabled(self, product_id: int, new_state: bool) -> None:
        db.set_product_enabled(product_id, new_state)
        logger.info("Product %d enabled=%s", product_id, new_state)

    # ------------------------------------------------------------------
    # Monitor callbacks (dispatched on main thread via root.after)
    # ------------------------------------------------------------------

    def _on_state_change(
        self,
        sku_url: str,
        old_state: str,
        new_state: str,
        ts: datetime,
        is_restock: bool,
    ) -> None:
        checked_str = ts.strftime("%H:%M:%S")
        self._table.update_status(sku_url, new_state, checked_str)

        if is_restock:
            self._table.flash_restock(sku_url)
            # Schedule un-flash after 3 seconds
            self.after(3000, lambda u=sku_url, s=new_state, c=checked_str:
                       self._table.update_status(u, s, c))

    def _on_poll_tick(self, sku_url: str, ts: datetime) -> None:
        self._last_poll_ts = ts
        self._lbl_last.configure(text=f"Last poll: {ts.strftime('%H:%M:%S')}")

    def _on_monitor_error(self, message: str) -> None:
        self._lbl_engine.configure(text="● Error", text_color=_RED)
        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        messagebox.showerror("Monitor Error", message, parent=self)
        logger.error("Monitor error: %s", message)

    # ------------------------------------------------------------------
    # Log panel
    # ------------------------------------------------------------------

    def _start_log_poll(self) -> None:
        self._drain_log_queue()

    def _drain_log_queue(self) -> None:
        try:
            while True:
                record = self._log_queue.get_nowait()
                self._append_log(record["level"], record["message"])
        except queue.Empty:
            pass
        self.after(_LOG_POLL_MS, self._drain_log_queue)

    def _append_log(self, level: str, message: str) -> None:
        tb = self._log_box._textbox
        tb.configure(state="normal")

        # Trim if over limit
        line_count = int(tb.index("end-1c").split(".")[0])
        if line_count > _LOG_MAX_LINES:
            tb.delete("1.0", f"{line_count - _LOG_MAX_LINES}.0")

        tag = level if level in ("ERROR", "WARNING", "INFO", "DEBUG") else "INFO"
        tb.insert("end", message + "\n", tag)
        tb.see("end")
        tb.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("0.0", "end")
        self._log_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_count(self) -> None:
        count = len(db.get_all_products())
        self._lbl_count.configure(text=f"{count} product{'s' if count != 1 else ''}")

    def _check_first_run(self) -> None:
        from app.services import keyring_service as ks
        products = db.get_all_products()
        has_notifier = bool(
            ks.get_discord_webhook() or
            (ks.get_telegram_token() and ks.get_telegram_chat_id())
        )
        if not products and not has_notifier:
            self.after(300, self._show_welcome)

    def _show_welcome(self) -> None:
        msg = (
            "Welcome to Pokemon Center UK Monitor.\n\n"
            "To get started:\n"
            "  1. Click Settings and configure at least one\n"
            "     notification channel (Discord or Telegram).\n\n"
            "  2. Click '+ Add Product' and paste a product URL.\n\n"
            "  3. Click  ▶ Start  to begin monitoring."
        )
        messagebox.showinfo("Getting Started", msg, parent=self)

    def _on_close(self) -> None:
        if self._controller and self._controller.is_running:
            self._controller.stop()
        self.destroy()
