"""
Settings dialog.

Two sections:
  1. Notifications — Discord webhook, Telegram token + chat ID.
     Values read/written via keyring_service (never stored plaintext in logs).
  2. Polling — intervals, jitter, browser auto-open, proxy.
     Values read/written via database settings table.

The Test Notification button fires a live test through the configured channels.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from app.services import keyring_service as ks
from app.storage import database as db

logger = logging.getLogger(__name__)


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Settings")
        self.geometry("540x560")
        self.resizable(False, False)
        self.grab_set()
        self.focus_set()
        self.lift()

        self._build()
        self._load_values()
        self._center(parent)

    def _build(self) -> None:
        # --- Scrollable container ---
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=10)

        # Notifications section
        ctk.CTkLabel(scroll, text="Notifications", font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(scroll, text="Discord Webhook URL", anchor="w").pack(fill="x")
        self._discord_var = tk.StringVar()
        self._discord_entry = ctk.CTkEntry(scroll, textvariable=self._discord_var, show="*", width=480)
        self._discord_entry.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(scroll, text="Telegram Bot Token", anchor="w").pack(fill="x")
        self._tg_token_var = tk.StringVar()
        self._tg_token_entry = ctk.CTkEntry(scroll, textvariable=self._tg_token_var, show="*", width=480)
        self._tg_token_entry.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(scroll, text="Telegram Chat ID", anchor="w").pack(fill="x")
        self._tg_chat_var = tk.StringVar()
        ctk.CTkEntry(scroll, textvariable=self._tg_chat_var, width=480).pack(fill="x", pady=(0, 8))

        ctk.CTkButton(scroll, text="Test Notification", width=160, command=self._test_notification).pack(anchor="w", pady=(0, 16))

        # Divider
        ctk.CTkFrame(scroll, height=1, fg_color="#444").pack(fill="x", pady=8)

        # Polling section
        ctk.CTkLabel(scroll, text="Polling", font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x", pady=(0, 6))

        self._poll_var      = self._labeled_entry(scroll, "Normal interval (seconds, used when In Stock)", "30")
        self._poll_fast_var = self._labeled_entry(scroll, "Fast interval (seconds, used when Out of Stock)", "10")
        self._jitter_var    = self._labeled_entry(scroll, "Max jitter (seconds added randomly each poll)", "5")

        ctk.CTkLabel(scroll, text="HTTP Proxy (optional, e.g. http://user:pass@host:port)", anchor="w").pack(fill="x", pady=(4, 0))
        self._proxy_var = tk.StringVar()
        ctk.CTkEntry(scroll, textvariable=self._proxy_var, width=480).pack(fill="x", pady=(0, 8))

        self._browser_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(scroll, text="Auto-open browser on restock detection", variable=self._browser_var).pack(anchor="w", pady=(0, 16))

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="Cancel", width=100, fg_color="#555", command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btn_frame, text="Save", width=100, command=self._save).pack(side="right")

    @staticmethod
    def _labeled_entry(parent, label: str, default: str) -> tk.StringVar:
        ctk.CTkLabel(parent, text=label, anchor="w").pack(fill="x", pady=(4, 0))
        var = tk.StringVar(value=default)
        ctk.CTkEntry(parent, textvariable=var, width=120).pack(anchor="w", pady=(0, 8))
        return var

    def _load_values(self) -> None:
        # Secrets — show placeholder if set
        dw = ks.get_discord_webhook()
        if dw:
            self._discord_var.set(dw)

        tt = ks.get_telegram_token()
        if tt:
            self._tg_token_var.set(tt)

        tc = ks.get_telegram_chat_id()
        if tc:
            self._tg_chat_var.set(tc)

        # Settings
        s = db.load_settings()
        self._poll_var.set(str(s.poll_interval))
        self._poll_fast_var.set(str(s.poll_interval_fast))
        self._jitter_var.set(str(s.poll_jitter_max))
        self._proxy_var.set(s.http_proxy or "")
        self._browser_var.set(s.auto_open_browser)

    def _save(self) -> None:
        # Validate numeric fields
        try:
            poll      = float(self._poll_var.get())
            poll_fast = float(self._poll_fast_var.get())
            jitter    = float(self._jitter_var.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Polling intervals must be numbers.", parent=self)
            return

        if poll < 5 or poll_fast < 5:
            messagebox.showerror(
                "Invalid Input",
                "Polling intervals must be at least 5 seconds.",
                parent=self,
            )
            return

        # Save secrets
        ks.set_discord_webhook(self._discord_var.get())
        ks.set_telegram_token(self._tg_token_var.get())
        ks.set_telegram_chat_id(self._tg_chat_var.get())

        # Save settings
        s = db.Settings(
            poll_interval=poll,
            poll_interval_fast=poll_fast,
            poll_jitter_max=jitter,
            auto_open_browser=self._browser_var.get(),
            http_proxy=self._proxy_var.get().strip(),
        )
        db.save_settings(s)
        logger.info("Settings saved")
        self.destroy()

    def _test_notification(self) -> None:
        """Send a test alert through all configured channels."""
        discord = self._discord_var.get().strip()
        tg_token = self._tg_token_var.get().strip()
        tg_chat  = self._tg_chat_var.get().strip()

        if not discord and not (tg_token and tg_chat):
            messagebox.showwarning(
                "No Channels Configured",
                "Enter at least one notification channel before testing.",
                parent=self,
            )
            return

        def _run() -> None:
            async def _send() -> list[str]:
                import httpx
                errors = []
                async with httpx.AsyncClient(timeout=10.0) as client:
                    if discord:
                        try:
                            r = await client.post(discord, json={"content": "Pokemon Center Monitor — test notification"})
                            if r.status_code not in (200, 204):
                                errors.append(f"Discord: HTTP {r.status_code}")
                        except Exception as exc:
                            errors.append(f"Discord: {exc}")

                    if tg_token and tg_chat:
                        try:
                            r = await client.post(
                                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                                json={"chat_id": tg_chat, "text": "Pokemon Center Monitor — test notification"},
                            )
                            data = r.json()
                            if not data.get("ok"):
                                errors.append(f"Telegram: {data}")
                        except Exception as exc:
                            errors.append(f"Telegram: {exc}")
                return errors

            errors = asyncio.run(_send())
            if errors:
                self.after(0, lambda: messagebox.showerror("Test Failed", "\n".join(errors), parent=self))
            else:
                self.after(0, lambda: messagebox.showinfo("Test Sent", "Test notification sent successfully.", parent=self))

        threading.Thread(target=_run, daemon=True).start()

    def _center(self, parent) -> None:
        self.update_idletasks()
        px = parent.winfo_x() + parent.winfo_width()  // 2
        py = parent.winfo_y() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"{w}x{h}+{px - w//2}+{py - h//2}")
