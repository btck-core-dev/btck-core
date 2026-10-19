#!/usr/bin/env python3
"""Native BTC-K desktop wallet UI built on the audited wallet engine."""

from __future__ import annotations

import threading
import time
import multiprocessing
import os
import sys
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from types import SimpleNamespace

import btck_cli
from privacy.bip39 import generate_mnemonic, validate_mnemonic
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import SATK_PER_COIN, StealthWallet
from privacy.wallet_registry import WalletRegistry, WalletRegistryError
from privacy.private_files import restrict_private_path


UI_FONT = {"win32": "Segoe UI", "darwin": "Helvetica Neue"}.get(sys.platform, "DejaVu Sans")
MONO_FONT = {"win32": "Consolas", "darwin": "Menlo"}.get(sys.platform, "DejaVu Sans Mono")
HAND_CURSOR = "pointinghand" if sys.platform == "darwin" else "hand2"


class TechButton(tk.Frame):
    """Theme-stable button that is not recolored by macOS Aqua."""

    def __init__(
        self, parent, text: str, command, *, bg: str, fg: str,
        hover_bg: str, border: str, disabled_bg: str, disabled_fg: str,
        padx: int, pady: int,
    ):
        super().__init__(parent, bg=border, bd=0, highlightthickness=0)
        self.command = command
        self.normal_bg = bg
        self.normal_fg = fg
        self.hover_bg = hover_bg
        self.border = border
        self.disabled_bg = disabled_bg
        self.disabled_fg = disabled_fg
        self.enabled = True
        self.label = tk.Label(
            self, text=text, bg=bg, fg=fg, bd=0,
            font=(UI_FONT, 11, "bold"), cursor=HAND_CURSOR,
            padx=padx, pady=pady,
        )
        self.label.pack(fill="both", expand=True, padx=1, pady=1)
        for widget in (self, self.label):
            widget.bind("<Button-1>", self._invoke)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _invoke(self, _event=None):
        if self.enabled:
            self.command()

    def _enter(self, _event=None):
        if self.enabled:
            self.label.configure(bg=self.hover_bg)

    def _leave(self, _event=None):
        self.label.configure(bg=self.normal_bg if self.enabled else self.disabled_bg)

    def configure(self, cnf=None, **kwargs):
        state = kwargs.pop("state", None)
        if state is not None:
            self.enabled = str(state) != "disabled"
            self.label.configure(
                bg=self.normal_bg if self.enabled else self.disabled_bg,
                fg=self.normal_fg if self.enabled else self.disabled_fg,
                cursor=HAND_CURSOR if self.enabled else "arrow",
            )
            super().configure(bg=self.border)
        if cnf or kwargs:
            return super().configure(cnf, **kwargs)
        return None

    config = configure


class MultilineDialog(tk.Toplevel):
    def __init__(self, parent, title: str, prompt: str, *, readonly_text: str = ""):
        super().__init__(parent)
        self.result = None
        self.title(title)
        self.geometry("720x420")
        self.minsize(580, 340)
        self.configure(bg="#080d18")
        self.transient(parent)
        self.grab_set()
        ttk.Label(self, text=prompt, wraplength=640, justify="left").pack(
            padx=24, pady=(24, 12), anchor="w"
        )
        self.text = tk.Text(
            self, height=10, wrap="word", bg="#0d1728", fg="#eaf6ff",
            insertbackground="#28d7e5", relief="flat", padx=16, pady=16,
            highlightthickness=1, highlightbackground="#20334c",
            font=(MONO_FONT, 13),
        )
        self.text.pack(fill="both", expand=True, padx=24, pady=8)
        if readonly_text:
            self.text.insert("1.0", readonly_text)
            self.text.configure(state="disabled")
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=24, pady=(8, 20))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Continue", command=self.accept).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.wait_visibility()
        if not readonly_text:
            self.text.focus_set()
        parent.wait_window(self)

    def accept(self):
        self.result = self.text.get("1.0", "end").strip()
        self.destroy()


class PasswordDialog(tk.Toplevel):
    """Wallet-owned password prompt that avoids broken macOS Tk dialogs."""

    def __init__(self, parent, prompt: str):
        super().__init__(parent)
        self.result: str | None = None
        self._finished = False
        self.title("Wallet Password")
        self.geometry("430x210")
        self.resizable(False, False)
        self.configure(bg="#080d18")
        self.transient(parent)

        tk.Label(
            self, text=prompt, bg="#080d18", fg="#e8f3ff",
            font=(UI_FONT, 13, "bold"), anchor="w",
        ).pack(fill="x", padx=26, pady=(25, 10))
        self.entry = tk.Entry(
            self, show="•", bg="#0d1728", fg="#eaf6ff",
            insertbackground="#25d4e3", relief="flat", bd=0,
            highlightthickness=1, highlightbackground="#20334c",
            highlightcolor="#25d4e3", font=(MONO_FONT, 14),
        )
        self.entry.pack(fill="x", padx=26, ipady=9)

        row = tk.Frame(self, bg="#080d18")
        row.pack(fill="x", padx=26, pady=(20, 22))
        self.cancel_button = TechButton(
            row, "CANCEL", self.cancel,
            bg="#0e1a2b", fg="#e8f3ff", hover_bg="#15263b",
            border="#20334c", disabled_bg="#0c1624", disabled_fg="#40546a",
            padx=20, pady=9,
        )
        self.cancel_button.pack(side="right")
        self.submit_button = TechButton(
            row, "OK", self.accept,
            bg="#25d4e3", fg="#031216", hover_bg="#58e6ef",
            border="#25d4e3", disabled_bg="#0c1624", disabled_fg="#40546a",
            padx=27, pady=9,
        )
        self.submit_button.pack(side="right", padx=(0, 10))

        self.bind("<Return>", self.accept)
        self.bind("<Escape>", self.cancel)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")
        self.grab_set()
        self.entry.focus_set()
        parent.wait_window(self)

    def accept(self, _event=None):
        if self._finished:
            return
        self._finished = True
        self.result = self.entry.get()
        self.destroy()

    def cancel(self, _event=None):
        if self._finished:
            return
        self._finished = True
        self.result = None
        self.destroy()


class WalletApp(tk.Tk):
    AUTO_SYNC_POLL_MS = 5_000
    AUTO_SYNC_MAX_BACKOFF_MS = 60_000
    BG = "#060a12"
    SIDEBAR = "#080e19"
    PANEL = "#0b1422"
    PANEL_ALT = "#0e1a2b"
    BORDER = "#182b40"
    CYAN = "#25d4e3"
    BLUE = "#4d8dff"
    GOLD = "#d9aa57"
    TEXT = "#e8f3ff"
    MUTED = "#7890a7"
    FAINT = "#425970"

    def __init__(self):
        super().__init__()
        self.title(f"BTC-K Wallet {btck_cli.WALLET_VERSION}")
        self.geometry("1120x740")
        self.minsize(980, 660)
        self.configure(bg=self.BG)
        self.wallet: StealthWallet | None = None
        self.wallet_path: Path | None = None
        self.network: dict | None = None
        # ``height`` is reserved for a live, completed synchronization and is
        # therefore the only height that may authorize spending.  A restored
        # encrypted checkpoint is useful for displaying known funds, but must
        # never silently enable SEND.
        self.height = -1
        self.ledger_tip_hash = ""
        self.cache_height = -1
        self.busy = False
        self.display_unit = "btck"
        self.sync_progress_value = 0.0
        self.active_wallet_id: str | None = None
        self.wallet_ids: list[str] = []
        self._refreshing_wallet_list = False
        self._wallet_generation = 0
        self._auto_sync_after_id: str | None = None
        self._tip_check_in_progress = False
        self._auto_sync_failures = 0
        self._closing = False
        self._password_prompt_active = False
        # Tk calls are not safe from worker threads on every macOS/Tk build.
        # Workers only enqueue callables; this poller executes them on Tk's
        # main thread.  Calling ``after`` directly from a worker can silently
        # lose the completion callback and leave the UI stuck in UNLOCKING.
        self._ui_queue: queue.Queue[object] = queue.Queue()
        self.registry_error = ""
        try:
            self.registry: WalletRegistry | None = WalletRegistry()
        except WalletRegistryError as exc:
            self.registry = None
            self.registry_error = str(exc)
        self._configure_style()
        self._build_ui()
        self.after(25, self._drain_ui_queue)
        self.protocol("WM_DELETE_WINDOW", self._close_application)
        self._refresh_wallet_list()
        if self.registry_error:
            self._log(f"Wallet registry unavailable: {self.registry_error}")
            self.status_var.set("Wallet registry unavailable; existing files remain unchanged.")

    def _configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=self.BG, foreground=self.TEXT, font=(UI_FONT, 12))
        style.configure("TFrame", background=self.BG)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT)
        style.configure("TButton", padding=(14, 9), background=self.PANEL_ALT, foreground=self.TEXT)
        style.map("TButton", background=[("active", "#15263b")])
        style.configure("TEntry", fieldbackground=self.PANEL, foreground=self.TEXT, insertcolor=self.CYAN)

    def _button(self, parent, text: str, command, *, primary: bool = False, compact: bool = False):
        button = TechButton(
            parent, text, command,
            bg=self.CYAN if primary else self.PANEL_ALT,
            fg="#031216" if primary else self.TEXT,
            hover_bg="#58e6ef" if primary else "#15263b",
            border=self.CYAN if primary else self.BORDER,
            disabled_bg="#0c1624", disabled_fg="#40546a",
            padx=14 if compact else 18, pady=8 if compact else 11,
        )
        return button

    def _panel(self, parent, *, bg: str | None = None):
        border = tk.Frame(parent, bg=self.BORDER, bd=0)
        body = tk.Frame(border, bg=bg or self.PANEL, bd=0)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        return border, body

    def _draw_network_mark(self, canvas: tk.Canvas):
        nodes = ((9, 14), (31, 7), (50, 17), (16, 42), (42, 45), (57, 34))
        links = ((0, 1), (1, 2), (0, 3), (1, 4), (2, 5), (3, 4), (4, 5))
        for a, b in links:
            canvas.create_line(*nodes[a], *nodes[b], fill="#17475d", width=1)
        for index, (x, y) in enumerate(nodes):
            color = self.GOLD if index == 4 else self.CYAN
            canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline="")
        canvas.create_text(32, 27, text="K", fill=self.TEXT, font=(UI_FONT, 24, "bold"))

    def _build_ui(self):
        shell = tk.Frame(self, bg=self.BG)
        shell.pack(fill="both", expand=True)

        sidebar = tk.Frame(shell, bg=self.SIDEBAR, width=222)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=self.SIDEBAR)
        brand.pack(fill="x", padx=22, pady=(25, 30))
        logo = tk.Canvas(brand, width=64, height=56, bg=self.SIDEBAR, highlightthickness=0)
        logo.pack(side="left")
        self._draw_network_mark(logo)
        brand_text = tk.Frame(brand, bg=self.SIDEBAR)
        brand_text.pack(side="left", padx=(5, 0))
        tk.Label(brand_text, text="BITCOIN K", bg=self.SIDEBAR, fg=self.TEXT,
                 font=(UI_FONT, 17, "bold")).pack(anchor="w")
        tk.Label(brand_text, text="CORE WALLET", bg=self.SIDEBAR, fg=self.CYAN,
                 font=(MONO_FONT, 9, "bold")).pack(anchor="w", pady=(2, 0))

        tk.Label(sidebar, text="WALLET CONTROL", bg=self.SIDEBAR, fg=self.FAINT,
                 font=(MONO_FONT, 9, "bold")).pack(anchor="w", padx=25, pady=(0, 10))
        self.open_button = self._button(sidebar, "OPEN WALLET", self.open_wallet, primary=True)
        self.open_button.pack(fill="x", padx=22, pady=(0, 9))
        self._button(sidebar, "CREATE NEW", self.create_wallet).pack(fill="x", padx=22, pady=4)
        self._button(sidebar, "RESTORE", self.restore_wallet).pack(fill="x", padx=22, pady=4)
        self.lock_button = self._button(sidebar, "LOCK SESSION", self.lock_wallet)
        self.lock_button.pack(fill="x", padx=22, pady=4)

        tk.Label(sidebar, text="MY WALLETS", bg=self.SIDEBAR, fg=self.FAINT,
                 font=(MONO_FONT, 9, "bold")).pack(anchor="w", padx=25, pady=(18, 8))
        self.wallet_list = tk.Listbox(
            sidebar, height=5, bg="#0a1320", fg=self.MUTED,
            selectbackground="#12354a", selectforeground=self.TEXT,
            activestyle="none", relief="flat", bd=0, highlightthickness=1,
            highlightbackground=self.BORDER, font=(UI_FONT, 10, "bold"),
            exportselection=False,
        )
        self.wallet_list.pack(fill="x", padx=22)
        self.wallet_list.bind("<<ListboxSelect>>", self._wallet_selected)
        wallet_tools = tk.Frame(sidebar, bg=self.SIDEBAR)
        wallet_tools.pack(fill="x", padx=22, pady=(7, 0))
        self.rename_button = self._button(wallet_tools, "RENAME", self.rename_selected_wallet, compact=True)
        self.rename_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.forget_button = self._button(wallet_tools, "FORGET", self.forget_selected_wallet, compact=True)
        self.forget_button.pack(side="left", fill="x", expand=True, padx=(4, 0))

        protocol = tk.Frame(sidebar, bg=self.SIDEBAR)
        protocol.pack(side="bottom", fill="x", padx=24, pady=24)
        tk.Label(protocol, text="PROTOCOL", bg=self.SIDEBAR, fg=self.FAINT,
                 font=(MONO_FONT, 9, "bold")).pack(anchor="w")
        tk.Label(protocol, text="BTCK MAINNET", bg=self.SIDEBAR, fg=self.TEXT,
                 font=(MONO_FONT, 10, "bold")).pack(anchor="w", pady=(7, 2))
        tk.Label(protocol, text="LOCAL KEY CUSTODY", bg=self.SIDEBAR, fg=self.MUTED,
                 font=(MONO_FONT, 9)).pack(anchor="w")
        tk.Label(protocol, text=f"CLIENT {btck_cli.WALLET_VERSION}", bg=self.SIDEBAR, fg=self.FAINT,
                 font=(MONO_FONT, 8)).pack(anchor="w", pady=(12, 0))

        content = tk.Frame(shell, bg=self.BG)
        content.pack(side="left", fill="both", expand=True, padx=32, pady=24)

        header = tk.Frame(content, bg=self.BG)
        header.pack(fill="x", pady=(0, 18))
        heading = tk.Frame(header, bg=self.BG)
        heading.pack(side="left")
        tk.Label(heading, text="ASSET CONSOLE", bg=self.BG, fg=self.TEXT,
                 font=(UI_FONT, 24, "bold")).pack(anchor="w")
        tk.Label(heading, text="Machine-native value. Human-controlled keys.", bg=self.BG,
                 fg=self.MUTED, font=(UI_FONT, 11)).pack(anchor="w", pady=(3, 0))
        status_pill = tk.Frame(header, bg="#0c2430", highlightthickness=1,
                               highlightbackground="#155164")
        status_pill.pack(side="right", pady=7)
        tk.Label(status_pill, text="●", bg="#0c2430", fg=self.CYAN,
                 font=(UI_FONT, 10)).pack(side="left", padx=(12, 5), pady=7)
        self.network_label = tk.Label(status_pill, text="MAINNET / LOCKED", bg="#0c2430",
                                      fg="#a8eaf0", font=(MONO_FONT, 9, "bold"))
        self.network_label.pack(side="left", padx=(0, 12), pady=7)

        hero_border, hero = self._panel(content, bg="#0a1726")
        hero_border.pack(fill="x")
        accent = tk.Frame(hero, bg=self.CYAN, height=2)
        accent.pack(fill="x")
        balance_area = tk.Frame(hero, bg="#0a1726")
        balance_area.pack(fill="x", padx=28, pady=(23, 18))
        tk.Label(balance_area, text="AVAILABLE BALANCE", bg="#0a1726", fg=self.MUTED,
                 font=(MONO_FONT, 9, "bold")).pack(anchor="w")
        balance_value_row = tk.Frame(balance_area, bg="#0a1726")
        balance_value_row.pack(fill="x", pady=(5, 0))
        self.balance_label = tk.Label(balance_value_row, text="0.00000000", bg="#0a1726", fg=self.TEXT,
                                      font=(UI_FONT, 40, "bold"))
        self.balance_label.pack(side="left", anchor="w")
        self.balance_unit_label = tk.Label(balance_value_row, text="BTC-K", bg="#0a1726", fg=self.CYAN,
                                           font=(MONO_FONT, 13, "bold"))
        self.balance_unit_label.pack(side="left", anchor="s", padx=(12, 0), pady=(0, 9))
        self.unit_button = self._button(balance_value_row, "SatK", self.toggle_balance_unit, compact=True)
        self.unit_button.pack(side="left", anchor="s", padx=(12, 0), pady=(0, 5))
        self.balance_detail_label = tk.Label(
            balance_area,
            text="TRACKED 0.00000000 BTC-K  •  LOCKED / IMMATURE 0.00000000 BTC-K",
            bg="#0a1726", fg=self.MUTED, font=(MONO_FONT, 9), anchor="w",
        )
        self.balance_detail_label.pack(fill="x", pady=(7, 0))

        self.address_var = tk.StringVar(value="Open an encrypted wallet to begin")
        address_row = tk.Frame(hero, bg="#0d1b2b")
        address_row.pack(fill="x", padx=28, pady=(0, 18))
        tk.Label(address_row, text="STEALTH ADDRESS", bg="#0d1b2b", fg=self.FAINT,
                 font=(MONO_FONT, 8, "bold")).pack(anchor="w", padx=14, pady=(10, 3))
        address_line = tk.Frame(address_row, bg="#0d1b2b")
        address_line.pack(fill="x", padx=14, pady=(0, 10))
        tk.Label(address_line, textvariable=self.address_var, bg="#0d1b2b", fg="#a9bdd0",
                 font=(MONO_FONT, 10), anchor="w").pack(side="left", fill="x", expand=True)
        self.copy_button = self._button(address_line, "COPY", self.copy_address, compact=True)
        self.copy_button.pack(side="right", padx=(12, 0))

        actions = tk.Frame(hero, bg="#0a1726")
        actions.pack(fill="x", padx=28, pady=(0, 24))
        self.send_button = self._button(actions, "SEND BTC-K", self.send_dialog, primary=True)
        self.send_button.pack(side="left")
        self.receive_button = self._button(actions, "RECEIVE", self.receive_dialog)
        self.receive_button.pack(side="left", padx=9)
        self.sync_button = self._button(actions, "SYNC LEDGER", self.sync_wallet)
        self.sync_button.pack(side="left")

        sync_border, sync_panel = self._panel(content, bg="#091522")
        sync_border.pack(fill="x", pady=(12, 0))
        sync_header = tk.Frame(sync_panel, bg="#091522")
        sync_header.pack(fill="x", padx=16, pady=(11, 6))
        tk.Label(sync_header, text="LEDGER SYNCHRONIZATION", bg="#091522", fg=self.TEXT,
                 font=(MONO_FONT, 9, "bold")).pack(side="left")
        self.sync_state_label = tk.Label(sync_header, text="LOCKED", bg="#091522", fg=self.FAINT,
                                         font=(MONO_FONT, 9, "bold"))
        self.sync_state_label.pack(side="right")
        self.sync_progress_canvas = tk.Canvas(
            sync_panel, height=8, bg="#091522", bd=0, highlightthickness=0,
        )
        self.sync_progress_canvas.pack(fill="x", padx=16)
        self.sync_progress_canvas.bind("<Configure>", lambda _event: self._draw_sync_progress())
        self.sync_detail_label = tk.Label(
            sync_panel, text="Unlock a wallet to synchronize its ledger history.",
            bg="#091522", fg=self.MUTED, anchor="w", font=(MONO_FONT, 9), width=1,
        )
        self.sync_detail_label.pack(fill="x", padx=16, pady=(6, 11))

        telemetry = tk.Frame(content, bg=self.BG)
        telemetry.pack(fill="x", pady=10)
        cells = []
        for index in range(3):
            border, body = self._panel(telemetry, bg=self.PANEL)
            border.pack(side="left", fill="x", expand=True, padx=(0 if index == 0 else 7, 0))
            cells.append(body)
        tk.Label(cells[0], text="LEDGER HEIGHT", bg=self.PANEL, fg=self.FAINT,
                 font=(MONO_FONT, 8, "bold")).pack(anchor="w", padx=15, pady=(12, 3))
        self.height_label = tk.Label(cells[0], text="NOT SYNCED", bg=self.PANEL, fg=self.TEXT,
                                     font=(MONO_FONT, 11, "bold"))
        self.height_label.pack(anchor="w", padx=15, pady=(0, 12))
        tk.Label(cells[1], text="KEY STATE", bg=self.PANEL, fg=self.FAINT,
                 font=(MONO_FONT, 8, "bold")).pack(anchor="w", padx=15, pady=(12, 3))
        self.key_state_label = tk.Label(cells[1], text="ENCRYPTED / LOCKED", bg=self.PANEL,
                                        fg=self.GOLD, font=(MONO_FONT, 11, "bold"))
        self.key_state_label.pack(anchor="w", padx=15, pady=(0, 12))
        tk.Label(cells[2], text="SYNC PRIVACY", bg=self.PANEL, fg=self.FAINT,
                 font=(MONO_FONT, 8, "bold")).pack(anchor="w", padx=15, pady=(12, 3))
        tk.Label(cells[2], text="ADDRESS-NEUTRAL", bg=self.PANEL, fg=self.CYAN,
                 font=(MONO_FONT, 11, "bold")).pack(anchor="w", padx=15, pady=(0, 12))

        activity_border, activity = self._panel(content, bg=self.PANEL)
        activity_border.pack(fill="both", expand=True)
        activity_header = tk.Frame(activity, bg=self.PANEL)
        activity_header.pack(fill="x", padx=18, pady=(14, 3))
        tk.Label(activity_header, text="PROTOCOL ACTIVITY", bg=self.PANEL, fg=self.TEXT,
                 font=(UI_FONT, 13, "bold")).pack(side="left")
        tk.Label(activity_header, text="LOCAL EVENT LOG", bg=self.PANEL, fg=self.FAINT,
                 font=(MONO_FONT, 8)).pack(side="right")
        self.activity = tk.Text(
            activity, state="disabled", wrap="word", bg=self.PANEL, fg=self.MUTED,
            relief="flat", font=(MONO_FONT, 10), height=7, padx=2, pady=5,
            selectbackground="#18384d", insertbackground=self.CYAN,
            bd=0, highlightthickness=0,
        )
        self.activity.pack(fill="both", expand=True, padx=16, pady=(3, 10))

        self.status_var = tk.StringVar(value="Keys remain encrypted on this Mac. No browser wallet is used.")
        footer = tk.Frame(content, bg=self.BG)
        footer.pack(fill="x", pady=(11, 0))
        tk.Label(footer, text="●", bg=self.BG, fg=self.GOLD, font=(UI_FONT, 9)).pack(side="left")
        tk.Label(footer, textvariable=self.status_var, bg=self.BG, fg=self.MUTED,
                 font=(UI_FONT, 10)).pack(side="left", padx=(7, 0))
        self._update_controls()

    def _log(self, message: str):
        self.activity.configure(state="normal")
        self.activity.insert("end", message.rstrip() + "\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _post_ui(self, callback) -> None:
        """Transfer a worker result to Tk without touching Tcl off-thread."""
        self._ui_queue.put(callback)

    def _drain_ui_queue(self) -> None:
        if self._closing:
            return
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                # Keep the dispatcher alive and surface an unexpected UI-side
                # failure instead of leaving a permanent busy state.
                self._set_busy(False, "Wallet operation failed")
                self._log(f"Wallet operation failed: {exc}")
        self.after(25, self._drain_ui_queue)

    def _draw_sync_progress(self):
        if not hasattr(self, "sync_progress_canvas"):
            return
        canvas = self.sync_progress_canvas
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#142437", outline="")
        filled = round(width * min(100.0, max(0.0, self.sync_progress_value)) / 100.0)
        if filled:
            canvas.create_rectangle(0, 0, filled, height, fill=self.CYAN, outline="")

    def _set_sync_progress(self, percent: float, state: str, detail: str, *, color: str | None = None):
        self.sync_progress_value = min(100.0, max(0.0, float(percent)))
        self.sync_state_label.configure(text=state, fg=color or self.CYAN)
        self.sync_detail_label.configure(text=detail)
        self._draw_sync_progress()

    def _set_busy(self, busy: bool, status: str = ""):
        self.busy = busy
        if status:
            self.status_var.set(status)
        self._update_controls()

    def _update_controls(self):
        state = "normal" if self.wallet and not self.busy else "disabled"
        self.sync_button.configure(state=state)
        self.send_button.configure(state=state if self.height >= 0 else "disabled")
        self.receive_button.configure(state=state)
        self.copy_button.configure(state=state)
        self.lock_button.configure(state=state)
        if self.wallet:
            self.network_label.configure(text="MAINNET / UNLOCKED")
            self.key_state_label.configure(text="LOCAL / UNLOCKED", fg=self.CYAN)
        else:
            self.network_label.configure(text="MAINNET / LOCKED")
            self.key_state_label.configure(text="ENCRYPTED / LOCKED", fg=self.GOLD)

        selected_state = "normal" if self._selected_wallet_id() and self.registry else "disabled"
        self.rename_button.configure(state=selected_state)
        self.forget_button.configure(state=selected_state)

    def _selected_wallet_id(self) -> str | None:
        if not hasattr(self, "wallet_list"):
            return None
        selected = self.wallet_list.curselection()
        if not selected or selected[0] >= len(self.wallet_ids):
            return None
        return self.wallet_ids[selected[0]]

    def _refresh_wallet_list(self, select_id: str | None = None):
        if not hasattr(self, "wallet_list"):
            return
        selected_id = select_id or self._selected_wallet_id() or self.active_wallet_id
        self._refreshing_wallet_list = True
        try:
            self.wallet_list.delete(0, "end")
            self.wallet_ids = []
            records = self.registry.records if self.registry else []
            for index, record in enumerate(records):
                exists = Path(record["path"]).is_file()
                active = self.wallet is not None and record["id"] == self.active_wallet_id
                marker = "●" if active else ("○" if exists else "×")
                self.wallet_list.insert("end", f" {marker}  {record['label']}")
                self.wallet_ids.append(record["id"])
                if record["id"] == selected_id:
                    self.wallet_list.selection_set(index)
                    self.wallet_list.activate(index)
        finally:
            self._refreshing_wallet_list = False
        self._update_controls()

    def _register_wallet(self, path: Path, wallet: StealthWallet) -> None:
        if not self.registry:
            return
        # An encrypted import may have registered another wallet while this
        # application was already open. Preserve those records when saving.
        self.registry.load()
        resolved = path.expanduser().resolve()
        existing = next(
            (record for record in self.registry.records if record["path"] == str(resolved)),
            None,
        )
        label = existing["label"] if existing else (wallet.name or resolved.stem)
        record = self.registry.add(resolved, label)
        self.active_wallet_id = record["id"]
        self._refresh_wallet_list(record["id"])

    def _wallet_selected(self, _event=None):
        if (
            self._refreshing_wallet_list or self.busy
            or self._password_prompt_active or not self.registry
        ):
            return
        wallet_id = self._selected_wallet_id()
        if not wallet_id or (wallet_id == self.active_wallet_id and self.wallet is not None):
            self._update_controls()
            return
        record = self.registry.get(wallet_id)
        if not record:
            return
        path = Path(record["path"])
        if not path.is_file():
            messagebox.showerror(
                "Wallet File Missing",
                "The indexed wallet file cannot be found. Use FORGET to remove only this stale entry.",
                parent=self,
            )
            return
        # A modal Tk password dialog still dispatches timers and queued worker
        # results. Invalidate the old tip check before entering that event loop,
        # otherwise it can start a scan and make _open_wallet_path silently busy.
        self._stop_auto_sync(invalidate=True)
        self._password_prompt_active = True
        try:
            password = self._ask_password()
            if password is None:
                self._refresh_wallet_list(self.active_wallet_id)
                self._schedule_auto_sync(100)
                return
            self._open_wallet_path(path, password, wallet_id=wallet_id)
        finally:
            self._password_prompt_active = False

    def rename_selected_wallet(self):
        wallet_id = self._selected_wallet_id()
        record = self.registry.get(wallet_id) if self.registry and wallet_id else None
        if not record:
            return
        label = simpledialog.askstring(
            "Rename Wallet", "Display name:", initialvalue=record["label"], parent=self,
        )
        if label is None:
            return
        try:
            self.registry.rename(wallet_id, label)
            self._refresh_wallet_list(wallet_id)
        except WalletRegistryError as exc:
            messagebox.showerror("Rename Wallet", str(exc), parent=self)

    def forget_selected_wallet(self):
        wallet_id = self._selected_wallet_id()
        record = self.registry.get(wallet_id) if self.registry and wallet_id else None
        if not record:
            return
        if not messagebox.askyesno(
            "Remove From Wallet List",
            f"Remove “{record['label']}” from this app?\n\n"
            "The encrypted wallet file will not be deleted.",
            parent=self,
        ):
            return
        try:
            self.registry.remove(wallet_id)
            if wallet_id == self.active_wallet_id:
                self.active_wallet_id = None
                self.lock_wallet()
            self._refresh_wallet_list()
        except WalletRegistryError as exc:
            messagebox.showerror("Remove Wallet", str(exc), parent=self)

    def _ask_password(self, *, confirm: bool = False) -> str | None:
        prompt = "Create password (minimum 10 characters):" if confirm else "Wallet password:"
        password = PasswordDialog(self, prompt).result
        if password is None:
            return None
        if confirm and len(password) < 10:
            messagebox.showerror("Password", "Password must contain at least 10 characters.", parent=self)
            return None
        if not password:
            messagebox.showerror("Password", "Password cannot be empty.", parent=self)
            return None
        if confirm:
            repeated = PasswordDialog(self, "Enter the same password again:").result
            if repeated is None or not btck_cli.secrets_compare(password, repeated):
                messagebox.showerror("Password", "Passwords do not match.", parent=self)
                return None
        return password

    def _cancel_auto_sync_timer(self) -> None:
        if self._auto_sync_after_id is not None:
            try:
                self.after_cancel(self._auto_sync_after_id)
            except tk.TclError:
                pass
            self._auto_sync_after_id = None

    def _stop_auto_sync(self, *, invalidate: bool = False) -> None:
        self._cancel_auto_sync_timer()
        self._tip_check_in_progress = False
        self._auto_sync_failures = 0
        if invalidate:
            self._wallet_generation += 1

    def _schedule_auto_sync(self, delay_ms: int | None = None) -> None:
        self._cancel_auto_sync_timer()
        if self._closing or self.wallet is None:
            return
        generation = self._wallet_generation
        delay = self.AUTO_SYNC_POLL_MS if delay_ms is None else max(0, int(delay_ms))
        self._auto_sync_after_id = self.after(
            delay, lambda: self._auto_sync_tick(generation),
        )

    def _auto_sync_tick(self, generation: int) -> None:
        self._auto_sync_after_id = None
        if (
            self._closing or self.wallet is None
            or generation != self._wallet_generation
        ):
            return
        if self.busy or self._tip_check_in_progress or self._password_prompt_active:
            self._schedule_auto_sync(1_000)
            return
        self._tip_check_in_progress = True

        def work():
            try:
                tip = btck_cli.fetch_wallet_tip(timeout=5.0)
                self._post_ui(lambda: self._auto_tip_finished(generation, tip))
            except Exception as exc:
                self._post_ui(lambda error=exc: self._auto_tip_failed(generation, error))

        threading.Thread(target=work, daemon=True).start()

    def _auto_tip_finished(self, generation: int, tip: dict) -> None:
        if self._closing or generation != self._wallet_generation or self.wallet is None:
            return
        self._tip_check_in_progress = False
        if self._password_prompt_active:
            self._schedule_auto_sync(1_000)
            return
        self._auto_sync_failures = 0
        if (self.height < 0 or int(tip["tip_height"]) != self.height
            or tip["tip_hash"] != self.ledger_tip_hash):
            self.sync_wallet(automatic=True, generation=generation)
            return
        self.network = tip["network"]
        self.height_label.configure(text=f"LIVE #{self.height:,}", fg=self.CYAN)
        self._set_sync_progress(
            100, "LIVE", f"Watching MAINNET at height #{self.height:,}; balances update automatically.",
        )
        self.status_var.set(f"Live ledger monitoring • height #{self.height:,}")
        self._schedule_auto_sync()

    def _auto_tip_failed(self, generation: int, error: Exception) -> None:
        if self._closing or generation != self._wallet_generation or self.wallet is None:
            return
        self._tip_check_in_progress = False
        # Permit a safe rolling upgrade: an older edge may not expose the
        # lightweight tip route yet, but its authenticated sync route can
        # still complete the mandatory opening sync.
        if self.height < 0:
            self._log(f"Live cursor unavailable; using the ledger sync endpoint: {error}")
            self.sync_wallet(automatic=True, generation=generation)
            return
        self._schedule_auto_retry(generation, error)

    def _schedule_auto_retry(self, generation: int, error: Exception) -> None:
        if self._closing or generation != self._wallet_generation or self.wallet is None:
            return
        self._auto_sync_failures += 1
        delay = min(
            self.AUTO_SYNC_MAX_BACKOFF_MS,
            self.AUTO_SYNC_POLL_MS * (2 ** min(self._auto_sync_failures - 1, 4)),
        )
        self._log(f"Live update delayed; retrying automatically: {error}")
        if self.cache_height >= 0:
            # A failed live observation must not leave the old LIVE badge or
            # transaction controls enabled. Keep the cached balance/cursor;
            # successful automatic synchronization will re-establish LIVE.
            self.height = -1
            self.height_label.configure(
                text=f"CACHED #{self.cache_height:,}", fg=self.GOLD,
            )
            self._set_sync_progress(
                self.sync_progress_value, "RETRYING",
                f"Cached through height #{self.cache_height:,}; reconnecting automatically. SEND remains disabled.",
                color=self.GOLD,
            )
            self._update_controls()
        self.status_var.set(f"Live update unavailable • retrying in {delay // 1000}s")
        self._schedule_auto_sync(delay)

    def _close_application(self) -> None:
        self._closing = True
        self._stop_auto_sync(invalidate=True)
        if self.wallet:
            self.wallet._unlock_password = None
        self.destroy()

    def _save_new_wallet(self, wallet: StealthWallet, mnemonic: str, password: str, path: Path):
        self._stop_auto_sync(invalidate=True)
        btck_cli.write_private_json(
            path,
            btck_cli._encrypted_recoverable_wallet(wallet, password, word_count=len(mnemonic.split())),
        )
        restrict_private_path(path)
        wallet._source_path = path
        wallet._unlock_password = password
        if self.wallet and self.wallet is not wallet:
            self.wallet._unlock_password = None
        self.wallet = wallet
        self.wallet_path = path
        self.network = None
        self.height = -1
        self.cache_height = -1
        self.address_var.set(wallet.address)
        self._refresh_balance()
        self.height_label.configure(text="NOT SYNCED", fg=self.TEXT)
        self._set_sync_progress(
            0, "READY", "Wallet created. Synchronize to discover confirmed outputs.",
        )
        self._log(f"Wallet ready: {path.name}")
        self._register_wallet(path, wallet)
        self._update_controls()
        self._schedule_auto_sync(100)

    def create_wallet(self):
        path_text = filedialog.asksaveasfilename(
            parent=self, title="Create Encrypted BTC-K Wallet",
            initialfile="btck_wallet.json", defaultextension=".json",
            filetypes=[("BTC-K wallet", "*.json")],
        )
        if not path_text:
            return
        path = Path(path_text)
        if path.exists():
            messagebox.showerror("Wallet Exists", "Refusing to overwrite an existing wallet.", parent=self)
            return
        password = self._ask_password(confirm=True)
        if password is None:
            return
        mnemonic = generate_mnemonic(256)
        wallet = btck_cli._wallet_from_mnemonic(mnemonic, path.stem)
        shown = MultilineDialog(
            self, "Write Down Recovery Words",
            "Write these 24 words on paper in order. They are never stored in the wallet file and cannot be shown again.",
            readonly_text=mnemonic,
        )
        if shown.result is None:
            return
        repeated = MultilineDialog(
            self, "Verify Recovery Words",
            "Re-enter all 24 recovery words from your paper backup before the wallet is created.",
        ).result
        if repeated is None or not btck_cli.secrets_compare(" ".join(repeated.lower().split()), mnemonic):
            messagebox.showerror("Recovery Check Failed", "The words did not reproduce the new wallet. Nothing was saved.", parent=self)
            return
        try:
            self._save_new_wallet(wallet, mnemonic, password, path)
            messagebox.showinfo("Wallet Created", "Encrypted wallet created and recovery phrase verified.", parent=self)
        except Exception as exc:
            messagebox.showerror("Create Wallet", str(exc), parent=self)

    def restore_wallet(self):
        phrase = MultilineDialog(self, "Restore Wallet", "Enter the 12- or 24-word BIP-39 recovery phrase.").result
        if phrase is None:
            return
        phrase = " ".join(phrase.lower().split())
        if len(phrase.split()) not in (12, 24) or not validate_mnemonic(phrase):
            messagebox.showerror("Recovery Phrase", "The recovery phrase is not valid BIP-39.", parent=self)
            return
        path_text = filedialog.asksaveasfilename(
            parent=self, title="Save Restored Wallet", initialfile="restored_btck_wallet.json",
            defaultextension=".json", filetypes=[("BTC-K wallet", "*.json")],
        )
        if not path_text:
            return
        path = Path(path_text)
        if path.exists():
            messagebox.showerror("Wallet Exists", "Refusing to overwrite an existing wallet.", parent=self)
            return
        password = self._ask_password(confirm=True)
        if password is None:
            return
        try:
            wallet = btck_cli._wallet_from_mnemonic(phrase, path.stem)
            self._save_new_wallet(wallet, phrase, password, path)
            messagebox.showinfo("Wallet Restored", "Wallet restored and encrypted successfully.", parent=self)
        except Exception as exc:
            messagebox.showerror("Restore Wallet", str(exc), parent=self)

    def open_wallet(self):
        if self.busy or self._password_prompt_active:
            self.status_var.set("A wallet operation is in progress. Please wait before switching.")
            return
        self._stop_auto_sync(invalidate=True)
        self._password_prompt_active = True
        try:
            path_text = filedialog.askopenfilename(
                parent=self, title="Open BTC-K Wallet", filetypes=[("BTC-K wallet", "*.json"), ("All files", "*")]
            )
            if not path_text:
                self._schedule_auto_sync(100)
                return
            password = self._ask_password()
            if password is None:
                self._schedule_auto_sync(100)
                return
            self._open_wallet_path(Path(path_text), password)
        finally:
            self._password_prompt_active = False

    def _open_wallet_path(
        self, path: Path, password: str, *, wallet_id: str | None = None,
    ):
        if self.busy:
            return
        self._stop_auto_sync(invalidate=True)
        self._set_sync_progress(
            0, "UNLOCKING", "Decrypting the wallet and its local ledger checkpoint…",
            color=self.GOLD,
        )
        self._set_busy(True, "Unlocking wallet locally…")

        def work():
            try:
                wallet = StealthWallet.load_from_json(str(path), password=password)
                wallet._source_path = path
                wallet._unlock_password = password
                cache_error = ""
                cached = {"network": None, "next_height": 0}
                try:
                    cached = btck_cli._load_sync_cache(wallet)
                except Exception as exc:
                    cache_error = str(exc)
                self._post_ui(
                    lambda: self._open_wallet_finished(
                        path, wallet, cached, cache_error, wallet_id,
                    ),
                )
            except Exception as exc:
                self._post_ui(lambda error=exc: self._open_wallet_failed(error))

        threading.Thread(target=work, daemon=True).start()

    def _open_wallet_finished(
        self, path: Path, wallet: StealthWallet, cached: dict,
        cache_error: str, wallet_id: str | None,
    ):
        try:
            if self.wallet:
                self.wallet._unlock_password = None
            self.wallet = wallet
            self.wallet_path = path
            self.network = None
            self.height = -1
            self.cache_height = -1
            if cache_error:
                self._log(f"Cached balance unavailable: {cache_error}")
            else:
                self.network = cached.get("network")
                wallet._live_sync_state = cached
                wallet._live_sync_saved_at = time.monotonic()
                # Cached outputs are useful for display, but every new application
                # session must perform a live sync before signing a transaction.
                next_height = max(0, int(cached.get("next_height", 0)))
                self.cache_height = next_height - 1
            self.address_var.set(wallet.address)
            if self.cache_height >= 0:
                self.height_label.configure(
                    text=f"CACHE #{self.cache_height:,}", fg=self.GOLD,
                )
                self._set_sync_progress(
                    0, "INCOMPLETE",
                    f"Cached through height #{self.cache_height:,}. Balance shown from this checkpoint; SEND remains disabled.",
                    color=self.GOLD,
                )
            else:
                self.height_label.configure(text="NOT SYNCED", fg=self.TEXT)
                self._set_sync_progress(
                    0, "READY TO SYNC",
                    "No ledger checkpoint is stored. Synchronize to discover confirmed outputs.",
                )
            self.ledger_tip_hash = ""
            self._refresh_balance()
            self._log(f"Opened encrypted wallet: {self.wallet_path.name}")
            self.status_var.set("Wallet unlocked locally. Sync before sending.")
            if wallet_id:
                self.active_wallet_id = wallet_id
            self._register_wallet(path, wallet)
            self._set_busy(False)
            self._schedule_auto_sync(100)
        except Exception as exc:
            self._open_wallet_failed(exc)

    def _open_wallet_failed(self, exc: Exception):
        self._set_busy(False, "Wallet unlock failed")
        messagebox.showerror("Open Wallet", f"Unable to unlock wallet: {exc}", parent=self)
        self._refresh_wallet_list(self.active_wallet_id)
        self._schedule_auto_sync(100)

    def lock_wallet(self):
        self._stop_auto_sync(invalidate=True)
        if self.wallet:
            self.wallet._unlock_password = None
        self.wallet = None
        self.wallet_path = None
        self.network = None
        self.height = -1
        self.cache_height = -1
        self.address_var.set("Open an encrypted wallet to begin")
        self._refresh_balance()
        self.height_label.configure(text="NOT SYNCED", fg=self.TEXT)
        self._set_sync_progress(
            0, "LOCKED", "Unlock a wallet to synchronize its ledger history.", color=self.FAINT,
        )
        self.status_var.set("Wallet locked. Keys removed from the active session.")
        self._log("Wallet locked.")
        self._refresh_wallet_list(self.active_wallet_id)
        self._update_controls()

    def _refresh_balance(
        self, *, tracked_satk: int | None = None, spendable_satk: int | None = None,
    ):
        if tracked_satk is None:
            tracked_satk = self.wallet.get_balance_satk() if self.wallet else 0
        if spendable_satk is None:
            spendable_satk = 0
            if self.wallet:
                display_height = self.height if self.height >= 0 else self.cache_height
                if display_height >= 0:
                    spendable_satk = self.wallet.get_spendable_balance_satk(display_height)
        locked_satk = max(0, tracked_satk - spendable_satk)
        if self.display_unit == "satk":
            self.balance_label.configure(text=f"{spendable_satk:,}")
            self.balance_unit_label.configure(text="SatK")
            self.unit_button.label.configure(text="BTC-K")
            self.balance_detail_label.configure(
                text=f"TRACKED {tracked_satk:,} SatK  •  LOCKED / IMMATURE {locked_satk:,} SatK"
            )
        else:
            self.balance_label.configure(text=f"{spendable_satk / SATK_PER_COIN:,.8f}")
            self.balance_unit_label.configure(text="BTC-K")
            self.unit_button.label.configure(text="SatK")
            self.balance_detail_label.configure(
                text=(
                    f"TRACKED {tracked_satk / SATK_PER_COIN:,.8f} BTC-K  •  "
                    f"LOCKED / IMMATURE {locked_satk / SATK_PER_COIN:,.8f} BTC-K"
                )
            )

    def toggle_balance_unit(self):
        self.display_unit = "satk" if self.display_unit == "btck" else "btck"
        self._refresh_balance()
        self.status_var.set(
            "Display unit changed only; 1 BTC-K = 100,000,000 SatK."
        )

    def sync_wallet(self, *, automatic: bool = False, generation: int | None = None):
        if not self.wallet or self.busy:
            return
        if generation is not None and generation != self._wallet_generation:
            return
        self._cancel_auto_sync_timer()
        generation = self._wallet_generation
        wallet = self.wallet
        incremental = self.cache_height >= 0
        self._set_sync_progress(
            0, "STARTING", "Loading the encrypted checkpoint and requesting the next ledger page…",
            color=self.GOLD,
        )
        self._set_busy(True, "Synchronizing address-neutral chain pages…")

        def work():
            try:
                args = SimpleNamespace(
                    sync_url=btck_cli.DEFAULT_SYNC_URL,
                    sync_page_size=1000,
                    sync_workers=(
                        1 if incremental
                        else max(1, min(8, (os.cpu_count() or 2) - 1))
                    ),
                    timeout=20.0,
                    live_session_cache=True,
                )
                # Shared cache receives public pages only; each wallet retains
                # separate encrypted ownership/UTXO state and local scanning.
                try:
                    from public_ledger_cache import PublicLedgerCache
                    args.verified_sync_tip=btck_cli.fetch_wallet_tip(timeout=5.0)
                    # A saved checkpoint can still be hundreds of thousands of
                    # blocks behind. Reserve single-worker mode for a small
                    # live delta, not every resumed initial download.
                    if int(args.verified_sync_tip['tip_height'])-self.cache_height>1000:
                        args.sync_workers=max(1,min(8,(os.cpu_count() or 2)-1))
                    args.public_ledger_cache=PublicLedgerCache(
                        Path.home()/'.btck'/'public-ledger.sqlite3',args.sync_url,
                        args.verified_sync_tip['network'])
                except Exception as cache_error:
                    # A cache or old tip endpoint must not destroy useful
                    # historical scanning. Fresh-tip verification below still
                    # prevents a false LIVE state.
                    args.public_ledger_cache=None
                snapshot, network = btck_cli.scan_wallet(
                    args, wallet,
                    progress_callback=lambda progress: self._post_ui(
                        lambda value=dict(progress): self._sync_progress(
                            value, generation, wallet,
                        ),
                    ),
                )
                tip=btck_cli.fetch_wallet_tip(timeout=5.0)
                snapshot['live_verified']=(network==tip['network']
                    and snapshot['selected_height']==tip['tip_height']
                    and snapshot.get('selected_hash')==tip['tip_hash'])
                snapshot['network_tip_height']=tip['tip_height']
                self._post_ui(
                    lambda: self._sync_finished(
                        snapshot, network, generation, wallet,
                    ),
                )
            except Exception as exc:
                self._post_ui(
                    lambda error=exc: self._sync_failed(
                        error, generation, wallet, automatic,
                    ),
                )

        threading.Thread(target=work, daemon=True).start()

    def _sync_progress(self, progress: dict, generation: int, wallet: StealthWallet):
        if generation != self._wallet_generation or wallet is not self.wallet:
            return
        completed = int(progress["completed"])
        total = int(progress["total"])
        percent = float(progress["percent"])
        tracked = int(progress["tracked_outputs"])
        self.cache_height = completed - 1
        self._refresh_balance(
            tracked_satk=int(progress["tracked_satk"]),
            spendable_satk=int(progress["spendable_satk"]),
        )
        self.height_label.configure(text=f"SYNC {percent:5.1f}%", fg=self.CYAN)
        self._set_sync_progress(
            percent, f"{percent:5.1f}%",
            f"Height {completed:,} of {total:,} • {tracked:,} wallet outputs identified",
        )
        self.status_var.set(
            f"Synchronizing ledger • {completed:,}/{total:,} • "
            f"{tracked:,} wallet outputs identified"
        )

    def _sync_finished(
        self, snapshot: dict, network: dict, generation: int, wallet: StealthWallet,
    ):
        if generation != self._wallet_generation or wallet is not self.wallet:
            return
        self.cache_height = int(snapshot["selected_height"])
        self.height = self.cache_height if snapshot.get('live_verified') else -1
        self.ledger_tip_hash = str(snapshot.get('selected_hash',''))
        self.network = network
        self._auto_sync_failures = 0
        self._refresh_balance()
        if self.height < 0:
            target=int(snapshot.get('network_tip_height',self.cache_height))
            self.height_label.configure(text=f"SYNC #{self.cache_height:,} / #{target:,}",fg=self.GOLD)
            self._set_sync_progress(99,"CATCHING UP",
                f"Wallet #{self.cache_height:,}; network #{target:,}. Updating automatically.",color=self.GOLD)
            self._set_busy(False,"Catching up to the live ledger; SEND remains disabled.")
            self._schedule_auto_sync(1_000)
            return
        self.height_label.configure(text=f"LIVE #{self.height:,}", fg=self.CYAN)
        self._set_sync_progress(
            100, "LIVE",
            f"Watching MAINNET at height #{self.height:,}; balances update automatically.",
        )
        self._log(f"Synchronized through ledger height #{self.height:,}.")
        self._set_busy(False, f"Live ledger monitoring • height #{self.height:,}")
        self._schedule_auto_sync()

    def _sync_failed(
        self, error: Exception, generation: int, wallet: StealthWallet, automatic: bool,
    ) -> None:
        if generation != self._wallet_generation or wallet is not self.wallet:
            return
        if automatic:
            self._set_busy(False)
            self._schedule_auto_retry(generation, error)
            return
        self._operation_failed("Sync failed", error)
        self._schedule_auto_sync()

    def _operation_failed(self, prefix: str, exc: Exception):
        if prefix == "Sync failed":
            self._set_sync_progress(
                self.sync_progress_value, "FAILED",
                "Synchronization stopped. The last encrypted checkpoint is preserved.",
                color="#ff6b78",
            )
        self._set_busy(False, prefix)
        self._log(f"{prefix}: {exc}")
        messagebox.showerror(prefix, str(exc), parent=self)

    def copy_address(self):
        if not self.wallet:
            return
        self.clipboard_clear()
        self.clipboard_append(self.wallet.address)
        self.status_var.set("Address copied. Verify it visually before use.")

    def receive_dialog(self):
        if not self.wallet:
            messagebox.showinfo("Receive", "Open a wallet first.", parent=self)
            return
        dialog = MultilineDialog(
            self, "Receive BTC-K",
            "Share only this public stealth address. Never share your wallet file, password, private keys, or recovery words.",
            readonly_text=self.wallet.address,
        )
        del dialog

    def send_dialog(self):
        if not self.wallet or self.network is None or self.height < 0:
            messagebox.showwarning("Send", "Synchronize the wallet before sending.", parent=self)
            return
        recipient = simpledialog.askstring("Send BTC-K", "Recipient BTC-K address:", parent=self)
        if recipient is None:
            return
        recipient = recipient.strip()
        if not StealthKeyPair.validate_stealth_address(recipient):
            messagebox.showerror("Recipient", "Invalid BTC-K stealth address.", parent=self)
            return
        amount = simpledialog.askstring("Send BTC-K", "Amount in BTC-K:", parent=self)
        if amount is None:
            return
        fee = simpledialog.askstring("Network Fee", "Fee in BTC-K:", initialvalue="0.0001", parent=self)
        if fee is None:
            return
        try:
            amount_satk = btck_cli.amount_to_satk(amount)
            fee_satk = btck_cli.amount_to_satk(fee)
        except Exception as exc:
            messagebox.showerror("Amount", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Confirm Signed Transfer",
            f"Recipient:\n{recipient}\n\nAmount: {amount_satk / SATK_PER_COIN:.8f} BTC-K\n"
            f"Fee: {fee_satk / SATK_PER_COIN:.8f} BTC-K\n\nSign locally and broadcast?",
            parent=self,
        ):
            return
        wallet = self.wallet
        spent_before = [utxo.spent for utxo in wallet.utxos]
        self._set_busy(True, "Signing locally and submitting signed transaction…")

        def work():
            try:
                tx = wallet.create_and_sign_transfer(recipient, amount_satk, fee_satk, current_height=self.height)
                package = btck_cli.make_signed_package(tx, self.network)
                args = SimpleNamespace(broadcast_url=btck_cli.DEFAULT_BROADCAST_URL, timeout=20.0)
                result = btck_cli.broadcast_signed_package(args, package)
                cache_warning = ""
                try:
                    btck_cli.persist_current_wallet_state(wallet)
                except Exception as exc:
                    # Broadcast acceptance is irreversible. Never report an
                    # accepted transfer as failed merely because local cache
                    # persistence failed; force a resync before another send.
                    cache_warning = str(exc)
                self._post_ui(
                    lambda: self._send_finished(
                        result, amount_satk, fee_satk, cache_warning,
                    ),
                )
            except btck_cli.BroadcastStatusUnknown as exc:
                # The relay may have accepted the package before the response
                # was lost. Keep the selected inputs marked spent until a full
                # sync resolves their on-chain/mempool state.
                cache_warning = ""
                try:
                    btck_cli.persist_current_wallet_state(wallet)
                except Exception as cache_exc:
                    cache_warning = str(cache_exc)
                self._post_ui(
                    lambda error=exc, warning=cache_warning: self._send_uncertain(
                        error, warning,
                    ),
                )
            except Exception as exc:
                for utxo, previous in zip(wallet.utxos, spent_before):
                    utxo.spent = previous
                self._post_ui(lambda error=exc: self._operation_failed("Transaction failed", error))

        threading.Thread(target=work, daemon=True).start()

    def _send_finished(
        self, result: dict, amount_satk: int, fee_satk: int, cache_warning: str = ""
    ):
        self._refresh_balance()
        self.cache_height = self.height
        self.height = -1
        self.network = None
        txid = str(result.get("txid") or "")
        self._log(
            f"Transaction accepted: {txid}\n"
            f"Sent {amount_satk / SATK_PER_COIN:.8f} BTC-K • fee {fee_satk / SATK_PER_COIN:.8f} BTC-K"
        )
        if cache_warning:
            self._log(f"Local cache warning: {cache_warning}. A full sync is required before another send.")
        self._set_sync_progress(
            0, "RESYNC REQUIRED", "Transaction accepted. Synchronize before another payment.",
            color=self.GOLD,
        )
        self._set_busy(False, f"Transaction accepted • sync again before another send • {txid[:16]}…")
        messagebox.showinfo("Transaction Accepted", f"Transaction ID:\n{txid}", parent=self)

    def _send_uncertain(self, error: Exception, cache_warning: str = ""):
        self._refresh_balance()
        self.cache_height = self.height
        self.height = -1
        self.network = None
        self._log(f"Broadcast status unknown: {error}")
        if cache_warning:
            self._log(f"Local cache warning: {cache_warning}")
        self._set_sync_progress(
            0, "RESYNC REQUIRED",
            "Broadcast status is unknown. Synchronize before attempting another payment.",
            color=self.GOLD,
        )
        self._set_busy(False, "Broadcast status unknown • synchronize before sending again")
        messagebox.showwarning(
            "Broadcast Status Unknown",
            "The relay response was not received. The transaction may already have been accepted.\n\n"
            "Do not send it again. Synchronize this wallet to confirm its status first.",
            parent=self,
        )


def main() -> None:
    if sys.argv[1:] == ["--install-desktop"]:
        from wallet_desktop import install_desktop_entry
        print(install_desktop_entry(Path(sys.executable)))
        return
    app = WalletApp()
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
