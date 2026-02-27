"""
Add Product dialog.

Collects: Product Name, URL.
SKU is extracted automatically from the URL path.
Validates that the URL starts with the expected domain.
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk


class AddProductDialog(ctk.CTkToplevel):
    """
    Modal dialog. After close, inspect .result:
      None      → user cancelled
      dict      → {'name': str, 'url': str, 'sku': str}
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Add Product")
        self.geometry("520x260")
        self.resizable(False, False)
        self.grab_set()          # modal
        self.focus_set()
        self.lift()

        self.result: Optional[dict] = None

        self._build()
        self._center(parent)

    def _build(self) -> None:
        pad = {"padx": 20, "pady": 6}

        ctk.CTkLabel(self, text="Product Name", anchor="w").pack(fill="x", **pad)
        self._name_var = tk.StringVar()
        self._name_entry = ctk.CTkEntry(self, textvariable=self._name_var, width=480)
        self._name_entry.pack(fill="x", **pad)
        self._name_entry.focus_set()

        ctk.CTkLabel(self, text="Product URL  (pokemoncenter.com/en-gb/...)", anchor="w").pack(fill="x", **pad)
        self._url_var = tk.StringVar()
        self._url_entry = ctk.CTkEntry(self, textvariable=self._url_var, width=480)
        self._url_entry.pack(fill="x", **pad)

        self._err_label = ctk.CTkLabel(self, text="", text_color="#f44336", anchor="w")
        self._err_label.pack(fill="x", padx=20)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(10, 15))
        ctk.CTkButton(btn_frame, text="Cancel", width=100, fg_color="#555", command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btn_frame, text="Add", width=100, command=self._submit).pack(side="right")

        self.bind("<Return>", lambda _: self._submit())
        self.bind("<Escape>", lambda _: self.destroy())

    def _submit(self) -> None:
        name = self._name_var.get().strip()
        url  = self._url_var.get().strip()

        if not name:
            self._err_label.configure(text="Product name is required.")
            return

        if not url:
            self._err_label.configure(text="URL is required.")
            return

        if not re.match(r"https?://(www\.)?pokemoncenter\.com", url, re.IGNORECASE):
            self._err_label.configure(text="URL must be from pokemoncenter.com")
            return

        # Auto-extract SKU from URL path segment
        sku = self._extract_sku(url)

        self.result = {"name": name, "url": url, "sku": sku}
        self.destroy()

    @staticmethod
    def _extract_sku(url: str) -> str:
        # Matches patterns like /product/290-80571/ or /290-80571
        m = re.search(r"/(\d{3}-\d{5,})", url)
        if m:
            return m.group(1)
        # Fallback: last non-empty path segment
        parts = [p for p in url.rstrip("/").split("/") if p]
        return parts[-1] if parts else ""

    def _center(self, parent) -> None:
        self.update_idletasks()
        px = parent.winfo_x() + parent.winfo_width()  // 2
        py = parent.winfo_y() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"{w}x{h}+{px - w//2}+{py - h//2}")
