"""Tkinter GUI for the STIR structure generator.

Single window:
  - Scenario textbox (multi-line)
  - Generate button
  - Preview pane (read-only, grouped with headings)
  - Copy to clipboard button (lines only, no headings)
  - Status line for "Parsing...", errors, etc.

Parser runs on a background thread so the window does not freeze during
the `claude -p` subprocess call (typically 3-8s on a desk PC).
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

import pyperclip

from .enumerator import enumerate_structures, groups_to_clipboard, groups_to_preview
from .parser import ParserError, parse_scenario

PLACEHOLDER = (
    "e.g. fade hawkish fomc sfrz6 tight around 97, flies and condors"
)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("KCP STIR Structure Generator")
        root.geometry("820x620")
        root.minsize(640, 480)

        # Result queue the background thread posts to; polled by the main
        # thread so all tk mutations stay on the main thread.
        self._result_q: queue.Queue = queue.Queue()
        self._clipboard_text: str = ""

        self._build_widgets()
        self.root.after(100, self._poll_worker)

    # -- widgets --------------------------------------------------------

    def _build_widgets(self) -> None:
        pad = {"padx": 10, "pady": 6}

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, **pad)

        ttk.Label(frame, text="Scenario:").pack(anchor="w")
        self.scenario = tk.Text(frame, height=5, wrap="word", font=("Segoe UI", 10))
        self.scenario.pack(fill="x")
        self._install_placeholder(self.scenario, PLACEHOLDER)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(6, 6))
        self.generate_btn = ttk.Button(btns, text="Generate", command=self._on_generate)
        self.generate_btn.pack(side="left")
        self.copy_btn = ttk.Button(btns, text="Copy to clipboard",
                                   command=self._on_copy, state="disabled")
        self.copy_btn.pack(side="left", padx=(8, 0))

        self.status = ttk.Label(frame, text="", foreground="#555")
        self.status.pack(anchor="w")

        ttk.Label(frame, text="Preview:").pack(anchor="w", pady=(8, 0))
        self.preview = tk.Text(frame, wrap="none", font=("Consolas", 10), state="disabled")
        self.preview.pack(fill="both", expand=True)

    def _install_placeholder(self, widget: tk.Text, text: str) -> None:
        widget.insert("1.0", text)
        widget.configure(foreground="#888")

        def on_focus_in(_):
            if widget.get("1.0", "end-1c") == text:
                widget.delete("1.0", "end")
                widget.configure(foreground="#000")

        def on_focus_out(_):
            if not widget.get("1.0", "end-1c").strip():
                widget.insert("1.0", text)
                widget.configure(foreground="#888")

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

        t = threading.Thread(target=self._worker, args=(text,), daemon=True)
        t.start()

    def _worker(self, text: str) -> None:
        try:
            params = parse_scenario(text)
            groups = enumerate_structures(params)
            self._result_q.put(("ok", (groups, params)))
        except ParserError as exc:
            self._result_q.put(("parser_error", str(exc)))
        except Exception as exc:  # enumerator/validation errors
            self._result_q.put(("error", f"{type(exc).__name__}: {exc}"))

    def _poll_worker(self) -> None:
        try:
            kind, payload = self._result_q.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_worker)
            return

        if kind == "ok":
            groups, params = payload
            if not groups:
                self._set_status("No structures generated — scenario may be ambiguous.",
                                 error=True)
            else:
                self._write_preview(groups_to_preview(groups, params))
                self._clipboard_text = groups_to_clipboard(groups, include_headings=True)
                total = sum(len(g["lines"]) for g in groups)
                self._set_status(f"Generated {total} structure(s) in {len(groups)} group(s).")
                self.copy_btn.configure(state="normal")
        elif kind == "parser_error":
            self._set_status(f"Parser error: {payload}", error=True)
        else:
            self._set_status(f"Error: {payload}", error=True)

        self.generate_btn.configure(state="normal")
        self.root.after(100, self._poll_worker)

    def _on_copy(self) -> None:
        if not self._clipboard_text:
            return
        pyperclip.copy(self._clipboard_text)
        lines = self._clipboard_text.count("\n") + 1
        self._set_status(f"Copied {lines} line(s). Ctrl+V into PM.")

    # -- helpers --------------------------------------------------------

    def _write_preview(self, text: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status.configure(text=text, foreground="#b00020" if error else "#555")


def run() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
