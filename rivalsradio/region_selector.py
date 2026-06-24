"""Fullscreen drag-to-select overlay for choosing the HUD capture region."""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from .config import CaptureRegion


def select_region(root: tk.Tk) -> Optional[CaptureRegion]:
    """Show a translucent fullscreen overlay; return the dragged rectangle.

    Returns None if the user cancels (Esc) or makes a zero-size selection.
    """
    overlay = tk.Toplevel(root)
    overlay.attributes("-fullscreen", True)
    try:
        overlay.attributes("-alpha", 0.3)
    except tk.TclError:
        pass
    overlay.configure(bg="black")
    overlay.attributes("-topmost", True)
    overlay.config(cursor="crosshair")

    canvas = tk.Canvas(overlay, bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        overlay.winfo_screenwidth() // 2, 40,
        text="Drag to select your HUD region — Esc to cancel",
        fill="white", font=("Segoe UI", 16),
    )

    state = {"x0": 0, "y0": 0, "rect": None, "result": None}

    def on_press(event):
        state["x0"], state["y0"] = event.x_root, event.y_root
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#1DB954", width=2
        )

    def on_drag(event):
        if state["rect"]:
            x0 = state["x0"] - overlay.winfo_rootx()
            y0 = state["y0"] - overlay.winfo_rooty()
            canvas.coords(state["rect"], x0, y0, event.x, event.y)

    def on_release(event):
        x1, y1 = event.x_root, event.y_root
        left, top = min(state["x0"], x1), min(state["y0"], y1)
        width, height = abs(x1 - state["x0"]), abs(y1 - state["y0"])
        if width > 5 and height > 5:
            state["result"] = CaptureRegion(left, top, width, height)
        overlay.destroy()

    def on_cancel(_event):
        overlay.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    overlay.bind("<Escape>", on_cancel)

    overlay.grab_set()
    root.wait_window(overlay)
    return state["result"]
