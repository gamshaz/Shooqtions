"""Tkinter GUI for the STIR structure generator.

Single window: scenario box, Generate, preview (bold purple headings),
Copy to clipboard. Modal popup for multi-event scenarios that need a
current futures price.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

import pyperclip

from .enumerator import enumerate_structures, groups_to_clipboard, groups_to_preview
from .parser import ParserError, parse_scenario
from .rates import scenario_needs_current_price

PLACEHOLDER = "e.g.  1 cut by december in sofr, flies broken in my favour"

# Dark palette.
BG       = "#0e1117"   # outer window
PANEL    = "#161b22"   # panels
FIELD    = "#1c2129"   # input fields
ACCENT   = "#3fb950"   # green
ACCENT2  = "#58a6ff"   # blue
MUTED    = "#6e7681"
TEXT     = "#e6edf3"
ERROR    = "#f85149"
HEADING  = "#d2a8ff"   # purple for section headings
STATUS   = "#8b949e"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("KCP STIR Structure Generator")
        root.geometry("960x740")
        root.minsize(760, 560)
        root.configure(bg=BG)

        self._result_q: queue.Queue = queue.Queue()
        self._clipboard_text: str = ""

        self._install_theme()
        self._build_widgets()
        self.root.after(100, self._poll_worker)

    # -- theme ----------------------------------------------------------

    def _install_theme(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT,
                        font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=BG, foreground=ACCENT,
                        font=("Segoe UI Semibold", 16))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=BG, foreground=STATUS,
                        font=("Consolas", 9))
        style.configure("Accent.TButton",
                        background=ACCENT, foreground="#0e1117",
                        font=("Segoe UI Semibold", 10),
                        padding=(18, 8), borderwidth=0)
        style.map("Accent.TButton",
                  background=[("active", "#2ea043"), ("disabled", "#30363d")],
                  foreground=[("disabled", MUTED)])
        style.configure("Secondary.TButton",
                        background="#21262d", foreground=TEXT,
                        font=("Segoe UI", 10), padding=(14, 7),
                        borderwidth=0)
        style.map("Secondary.TButton",
                  background=[("active", "#30363d"), ("disabled", "#161b22")],
                  foreground=[("disabled", MUTED)])
        style.configure("TSeparator", background="#30363d")

    # -- widgets --------------------------------------------------------

    def _build_widgets(self) -> None:
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=24, pady=(18, 6))
        ttk.Label(header, text="STIR Structure Generator", style="Title.TLabel"
                  ).pack(anchor="w")
        ttk.Label(header,
                  text="Natural-language scenario → PricingMonkey trade descriptions",
                  style="Subtitle.TLabel").pack(anchor="w")

        ttk.Separator(self.root).pack(fill="x", padx=24, pady=(8, 8))

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        ttk.Label(body, text="SCENARIO",
                  foreground=ACCENT2, background=BG,
                  font=("Segoe UI Semibold", 9)).pack(anchor="w")
        self.scenario = tk.Text(body, height=4, wrap="word",
                                font=("Segoe UI", 11),
                                bg=FIELD, fg=TEXT, insertbackground=TEXT,
                                relief="flat", borderwidth=0,
                                padx=12, pady=10,
                                highlightthickness=1,
                                highlightbackground="#30363d",
                                highlightcolor=ACCENT2)
        self.scenario.pack(fill="x", pady=(4, 8))
        self._install_placeholder(self.scenario, PLACEHOLDER)

        ctrl = ttk.Frame(body)
        ctrl.pack(fill="x", pady=(0, 8))
        self.generate_btn = ttk.Button(ctrl, text="Generate",
                                       style="Accent.TButton",
                                       command=self._on_generate)
        self.generate_btn.pack(side="left")
        self.copy_btn = ttk.Button(ctrl, text="Copy to clipboard",
                                   style="Secondary.TButton",
                                   command=self._on_copy, state="disabled")
        self.copy_btn.pack(side="left", padx=(10, 0))
        self.clear_btn = ttk.Button(ctrl, text="Clear",
                                    style="Secondary.TButton",
                                    command=self._on_clear)
        self.clear_btn.pack(side="left", padx=(10, 0))

        self.status = ttk.Label(ctrl, text="Ready.", style="Status.TLabel")
        self.status.pack(side="right")

        ttk.Label(body, text="PREVIEW",
                  foreground=ACCENT2, background=BG,
                  font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(8, 0))
        preview_wrap = ttk.Frame(body)
        preview_wrap.pack(fill="both", expand=True, pady=(4, 0))
        self.preview = tk.Text(preview_wrap, wrap="none",
                               font=("Consolas", 10),
                               bg=PANEL, fg=TEXT,
                               relief="flat", borderwidth=0,
                               padx=14, pady=10,
                               highlightthickness=1,
                               highlightbackground="#30363d",
                               state="disabled")
        self.preview.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(preview_wrap, orient="vertical",
                           command=self.preview.yview)
        sb.pack(side="right", fill="y")
        self.preview.configure(yscrollcommand=sb.set)
        self.preview.tag_configure("heading", foreground=HEADING,
                                   font=("Consolas", 10, "bold"))
        self.preview.tag_configure("banner", foreground=ACCENT,
                                   font=("Consolas", 9, "italic"))
        self.preview.tag_configure("error", foreground=ERROR,
                                   font=("Consolas", 10, "bold"))

    def _install_placeholder(self, widget: tk.Text, text: str) -> None:
        widget.insert("1.0", text)
        widget.configure(foreground=MUTED)

        def on_focus_in(_):
            if widget.get("1.0", "end-1c") == text:
                widget.delete("1.0", "end")
                widget.configure(foreground=TEXT)

        def on_focus_out(_):
            if not widget.get("1.0", "end-1c").strip():
                widget.insert("1.0", text)
                widget.configure(foreground=MUTED)

        widget.bind("<FocusIn>", on_focus_in)
        widget.bind("<FocusOut>", on_focus_out)

    # -- actions --------------------------------------------------------

    def _on_generate(self) -> None:
        text = self.scenario.get("1.0", "end-1c").strip()
        if not text or text == PLACEHOLDER:
            self._set_status("Type a scenario first.", error=True)
            return

        self.generate_btn.configure(state="disabled")
        self.copy_btn.configure(state="disabled")
        self._clipboard_text = ""
        self._write_preview("")
        self._set_status("Parsing...")

        threading.Thread(target=self._parse_worker, args=(text,),
                         daemon=True).start()

    def _parse_worker(self, text: str) -> None:
        try:
            params = parse_scenario(text)
            self._result_q.put(("parsed", (params, text)))
        except ParserError as exc:
            self._result_q.put(("parser_error", str(exc)))
        except Exception as exc:
            self._result_q.put(("error", f"{type(exc).__name__}: {exc}"))

    def _enum_worker(self, params: dict) -> None:
        try:
            groups = enumerate_structures(params)
            self._result_q.put(("ok", (groups, params)))
        except Exception as exc:
            self._result_q.put(("error", f"{type(exc).__name__}: {exc}"))

    def _poll_worker(self) -> None:
        try:
            kind, payload = self._result_q.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_worker)
            return

        if kind == "parsed":
            params, _text = payload
            if scenario_needs_current_price(params):
                self._ask_for_current_price(params)
            else:
                self._set_status("Enumerating structures...")
                threading.Thread(target=self._enum_worker,
                                 args=(params,), daemon=True).start()
        elif kind == "ok":
            groups, params = payload
            if not groups:
                self._set_status("No structures generated — scenario may be ambiguous.",
                                 error=True)
                self.generate_btn.configure(state="normal")
            else:
                self._render_preview(groups, params)
                self._clipboard_text = groups_to_clipboard(groups,
                                                           include_headings=True)
                total = sum(len(g["lines"]) for g in groups)
                self._set_status(f"Generated {total} structures in {len(groups)} groups.")
                self.copy_btn.configure(state="normal")
                self.generate_btn.configure(state="normal")
        elif kind == "parser_error":
            self._set_status(f"Parser error: {payload}", error=True)
            self.generate_btn.configure(state="normal")
        else:
            self._set_status(f"Error: {payload}", error=True)
            self.generate_btn.configure(state="normal")

        self.root.after(100, self._poll_worker)

    def _ask_for_current_price(self, params: dict) -> None:
        pe = f"{params.get('product', '?')}{params.get('expiry', '?')}"
        dialog = CurrentPriceDialog(self.root, pe, params)
        self.root.wait_window(dialog.top)
        if dialog.price is not None:
            params["current_price_override"] = dialog.price
            self._set_status(f"Using {pe} = {dialog.price}. Enumerating...")
        else:
            self._set_status("Using current_rates.json fallback. Enumerating...")
        threading.Thread(target=self._enum_worker, args=(params,),
                         daemon=True).start()

    def _on_copy(self) -> None:
        if not self._clipboard_text:
            return
        pyperclip.copy(self._clipboard_text)
        lines = self._clipboard_text.count("\n") + 1
        self._set_status(f"Copied {lines} lines. Ctrl+V into PM.")

    def _on_clear(self) -> None:
        self.scenario.delete("1.0", "end")
        self._write_preview("")
        self._clipboard_text = ""
        self.copy_btn.configure(state="disabled")
        self._set_status("Ready.")

    # -- rendering ------------------------------------------------------

    def _render_preview(self, groups, params: dict) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")

        banner = groups_to_preview([], params).split("\n")[0]
        self.preview.insert("end", banner + "\n\n", "banner")

        for g in groups:
            self.preview.insert("end", g["heading"] + "\n", "heading")
            for line in g["lines"]:
                self.preview.insert("end", line + "\n")
            self.preview.insert("end", "\n")

        self.preview.configure(state="disabled")

    def _write_preview(self, text: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if text:
            self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status.configure(text=text,
                              foreground=ERROR if error else STATUS)


class CurrentPriceDialog:
    """Modal popup asking for the current futures price."""

    def __init__(self, parent: tk.Tk, ticker: str, params: dict) -> None:
        self.price: float | None = None
        self.top = tk.Toplevel(parent)
        self.top.title("Need current futures price")
        self.top.configure(bg=BG)
        self.top.geometry("440x220")
        self.top.resizable(False, False)
        self.top.transient(parent)
        self.top.grab_set()

        events_blurb = ""
        if params.get("rate_events"):
            events_blurb = f"  ({len(params['rate_events'])} rate events)"

        frame = ttk.Frame(self.top)
        frame.pack(fill="both", expand=True, padx=24, pady=20)

        ttk.Label(frame, text="Multi-event scenario",
                  foreground=ACCENT, background=BG,
                  font=("Segoe UI Semibold", 11)).pack(anchor="w")
        ttk.Label(frame, text=f"What is the current {ticker} futures price?{events_blurb}",
                  background=BG, foreground=TEXT,
                  font=("Segoe UI", 10),
                  wraplength=380, justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Label(frame, text="Leave blank to fall back to current_rates.json.",
                  background=BG, foreground=MUTED,
                  font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 10))

        self.entry = tk.Entry(frame, font=("Segoe UI", 14),
                              bg=FIELD, fg=TEXT, insertbackground=TEXT,
                              relief="flat", borderwidth=0,
                              highlightthickness=1,
                              highlightbackground="#30363d",
                              highlightcolor=ACCENT2)
        self.entry.pack(fill="x", ipady=8)
        self.entry.focus_set()

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Continue", style="Accent.TButton",
                   command=self._accept).pack(side="right")
        ttk.Button(btns, text="Skip", style="Secondary.TButton",
                   command=self._skip).pack(side="right", padx=(0, 10))

        self.top.bind("<Return>", lambda _e: self._accept())
        self.top.bind("<Escape>", lambda _e: self._skip())

    def _accept(self) -> None:
        text = self.entry.get().strip()
        if not text:
            self._skip()
            return
        try:
            self.price = float(text)
        except ValueError:
            self.entry.configure(highlightbackground=ERROR,
                                 highlightcolor=ERROR)
            return
        self.top.destroy()

    def _skip(self) -> None:
        self.price = None
        self.top.destroy()


def run() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
