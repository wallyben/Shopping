"""
ProductTable — ttk.Treeview styled to match the CustomTkinter dark theme.

CustomTkinter has no native table widget. ttk.Treeview is used and styled
with ttk.Style to match CTk's dark palette. The Enabled column is click-to-
toggle via a <ButtonRelease-1> binding.

Columns:
  name        Product Name
  url         URL (truncated to 55 chars)
  status      IN_STOCK / OUT_OF_STOCK / UNKNOWN / BLOCKED
  checked     Last Checked (HH:MM:SS)
  enabled     ON / OFF (click to toggle)

Status colours (applied via Treeview tags):
  in_stock    → green foreground
  out_stock   → red foreground
  unknown     → orange/yellow foreground
  blocked     → red foreground (italic)
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Dark colour palette matched to CTk default dark theme
# ---------------------------------------------------------------------------
_BG          = "#2b2b2b"
_BG_ALT      = "#252525"
_HEADING_BG  = "#1a1a1a"
_FG          = "#dce4ee"
_SEL_BG      = "#1f6aa5"
_SEL_FG      = "#ffffff"
_GRID        = "#3a3a3a"

_GREEN       = "#4caf50"
_RED         = "#f44336"
_ORANGE      = "#ff9800"
_GREY        = "#9e9e9e"

_COL_WIDTHS = {
    "name":    240,
    "url":     320,
    "status":  110,
    "checked": 100,
    "enabled":  70,
}
_COLUMNS = list(_COL_WIDTHS.keys())
_HEADINGS = {
    "name":    "Product Name",
    "url":     "URL",
    "status":  "Status",
    "checked": "Last Checked",
    "enabled": "Enabled",
}


class ProductTable(tk.Frame):
    """
    Drop-in frame containing the Treeview + scrollbars.

    on_toggle_enabled(product_id: int, new_state: bool) — called when user
    clicks the Enabled cell of a row.
    """

    def __init__(self, parent, on_toggle_enabled: Callable[[int, bool], None]):
        super().__init__(parent, bg=_BG)
        self._on_toggle = on_toggle_enabled
        self._id_map: dict[str, int] = {}  # treeview iid → product DB id
        self._url_map: dict[str, str] = {}  # treeview iid → url
        self._build()

    def _build(self) -> None:
        self._apply_style()

        self._tree = ttk.Treeview(
            self,
            columns=_COLUMNS,
            show="headings",
            style="Dark.Treeview",
            selectmode="browse",
        )

        for col in _COLUMNS:
            self._tree.heading(col, text=_HEADINGS[col], anchor="w")
            self._tree.column(col, width=_COL_WIDTHS[col], minwidth=60, anchor="w")

        # Status colour tags
        self._tree.tag_configure("in_stock",  foreground=_GREEN)
        self._tree.tag_configure("out_stock", foreground=_RED)
        self._tree.tag_configure("unknown",   foreground=_ORANGE)
        self._tree.tag_configure("blocked",   foreground=_RED)
        self._tree.tag_configure("restock_flash", background="#1a3a1a")  # brief highlight

        vsb = ttk.Scrollbar(self, orient="vertical",   command=self._tree.yview, style="Dark.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self._tree.xview, style="Dark.Horizontal.TScrollbar")
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._tree.bind("<ButtonRelease-1>", self._on_click)

    @staticmethod
    def _apply_style() -> None:
        s = ttk.Style()
        s.theme_use("clam")

        s.configure(
            "Dark.Treeview",
            background=_BG,
            foreground=_FG,
            fieldbackground=_BG,
            borderwidth=0,
            rowheight=26,
            font=("Segoe UI", 10),
        )
        s.configure(
            "Dark.Treeview.Heading",
            background=_HEADING_BG,
            foreground=_FG,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
        )
        s.map(
            "Dark.Treeview",
            background=[("selected", _SEL_BG)],
            foreground=[("selected", _SEL_FG)],
        )
        s.configure(
            "Dark.Vertical.TScrollbar",
            troughcolor=_BG,
            background=_HEADING_BG,
            bordercolor=_BG,
            arrowcolor=_FG,
        )
        s.configure(
            "Dark.Horizontal.TScrollbar",
            troughcolor=_BG,
            background=_HEADING_BG,
            bordercolor=_BG,
            arrowcolor=_FG,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_products(self, products: list) -> None:
        """Replace all rows with the given product list."""
        self._tree.delete(*self._tree.get_children())
        self._id_map.clear()
        self._url_map.clear()
        for p in products:
            self._insert_product(p)

    def add_product(self, product) -> None:
        self._insert_product(product)

    def remove_product(self, product_id: int) -> None:
        iid = self._iid_for_db_id(product_id)
        if iid:
            self._tree.delete(iid)
            self._id_map.pop(iid, None)
            self._url_map.pop(iid, None)

    def update_status(self, url: str, status: str, last_checked: str) -> None:
        iid = self._iid_for_url(url)
        if not iid:
            return
        tag = self._status_tag(status)
        self._tree.set(iid, "status", self._fmt_status(status))
        self._tree.set(iid, "checked", last_checked)
        self._tree.item(iid, tags=(tag,))

    def flash_restock(self, url: str) -> None:
        """Briefly highlight a row green on restock detection."""
        iid = self._iid_for_url(url)
        if not iid:
            return
        self._tree.item(iid, tags=("restock_flash", "in_stock"))

    def selected_product_id(self) -> Optional[int]:
        sel = self._tree.selection()
        if not sel:
            return None
        return self._id_map.get(sel[0])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _insert_product(self, p) -> None:
        enabled_txt = "ON" if p.enabled else "OFF"
        tag = self._status_tag(p.last_status)
        iid = self._tree.insert(
            "",
            "end",
            values=(
                p.name,
                self._truncate_url(p.url),
                self._fmt_status(p.last_status),
                p.last_checked_display,
                enabled_txt,
            ),
            tags=(tag,),
        )
        self._id_map[iid] = p.id
        self._url_map[iid] = p.url

    def _on_click(self, event: tk.Event) -> None:
        region = self._tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self._tree.identify_column(event.x)
        # column("#5") = enabled column (1-indexed)
        if col != "#5":
            return
        iid = self._tree.identify_row(event.y)
        if not iid:
            return

        product_id = self._id_map.get(iid)
        if product_id is None:
            return

        current = self._tree.set(iid, "enabled")
        new_state = current != "ON"
        self._tree.set(iid, "enabled", "ON" if new_state else "OFF")
        self._on_toggle(product_id, new_state)

    def _iid_for_db_id(self, product_id: int) -> Optional[str]:
        for iid, pid in self._id_map.items():
            if pid == product_id:
                return iid
        return None

    def _iid_for_url(self, url: str) -> Optional[str]:
        for iid, u in self._url_map.items():
            if u == url:
                return iid
        return None

    @staticmethod
    def _status_tag(status: str) -> str:
        mapping = {
            "IN_STOCK":     "in_stock",
            "OUT_OF_STOCK": "out_stock",
            "BLOCKED":      "blocked",
        }
        return mapping.get(status, "unknown")

    @staticmethod
    def _fmt_status(status: str) -> str:
        mapping = {
            "IN_STOCK":     "In Stock",
            "OUT_OF_STOCK": "Out of Stock",
            "UNKNOWN":      "Unknown",
            "BLOCKED":      "Blocked",
        }
        return mapping.get(status, status)

    @staticmethod
    def _truncate_url(url: str, max_len: int = 55) -> str:
        if len(url) <= max_len:
            return url
        return url[:max_len - 3] + "..."
