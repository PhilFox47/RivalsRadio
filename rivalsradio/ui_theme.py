"""A modern flat dark theme for RivalsRadio's Tkinter UI.

Tkinter's native themes look dated because most widget options are ignored by
the platform engines. We switch to the fully restylable ``clam`` base and paint
a cohesive dark palette with Spotify-green accents — no third-party deps.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---- Palette -------------------------------------------------------------
BG = "#15161a"          # app background
SURFACE = "#1e2025"     # cards / header
SURFACE_HI = "#272a31"  # inputs, raised buttons
BORDER = "#33363d"
TEXT = "#e8eaed"
MUTED = "#9aa0a6"
ACCENT = "#1DB954"      # Spotify green
ACCENT_HOVER = "#1ed760"
ACCENT_ACTIVE = "#169c46"
ACCENT_INK = "#08210f"  # text on top of accent fills
DANGER = "#e5534b"
DANGER_HOVER = "#ef6b63"
WARN = "#e7b34b"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI", 17, "bold")
FONT_MONO = ("Consolas", 9)


def apply(root: tk.Tk) -> dict:
    """Apply the theme to ``root`` and return the palette as a dict."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # the only built-in base that honours our colors
    except tk.TclError:
        pass

    root.configure(bg=BG)

    # The Combobox dropdown is a classic Tk Listbox; style it via the option DB.
    root.option_add("*TCombobox*Listbox.background", SURFACE_HI)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", ACCENT_INK)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style.configure(
        ".",
        background=BG, foreground=TEXT, fieldbackground=SURFACE_HI,
        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
        troughcolor=BG, focuscolor=ACCENT, font=FONT, relief="flat",
    )

    # Frames / labels
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_SMALL)
    style.configure("Heading.TLabel", background=BG, foreground=TEXT, font=FONT_BOLD)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT, font=FONT)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=FONT_SMALL)
    style.configure("Title.TLabel", background=SURFACE, foreground=TEXT, font=FONT_TITLE)
    style.configure("TitleAccent.TLabel", background=SURFACE, foreground=ACCENT, font=FONT_TITLE)

    # Buttons — flat, padded, no 3D bevel
    style.configure(
        "TButton", background=SURFACE_HI, foreground=TEXT, font=FONT_BOLD,
        borderwidth=0, padding=(14, 8), relief="flat", anchor="center",
    )
    style.map(
        "TButton",
        background=[("pressed", BORDER), ("active", "#31353d"), ("disabled", SURFACE)],
        foreground=[("disabled", MUTED)],
    )

    style.configure("Accent.TButton", background=ACCENT, foreground=ACCENT_INK)
    style.map(
        "Accent.TButton",
        background=[("pressed", ACCENT_ACTIVE), ("active", ACCENT_HOVER), ("disabled", SURFACE)],
        foreground=[("disabled", MUTED)],
    )

    style.configure("Danger.TButton", background=DANGER, foreground="#2a0a08")
    style.map(
        "Danger.TButton",
        background=[("pressed", "#c4453e"), ("active", DANGER_HOVER)],
    )

    # Subtle "ghost" button for tiny destructive actions (the ✕ on a hero row)
    style.configure("Ghost.TButton", background=SURFACE, foreground=MUTED, padding=(8, 6))
    style.map(
        "Ghost.TButton",
        background=[("active", "#31353d")], foreground=[("active", DANGER)],
    )

    # Notebook tabs
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(6, 6, 6, 0))
    style.configure(
        "TNotebook.Tab", background=BG, foreground=MUTED, font=FONT_BOLD,
        padding=(18, 9), borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE)],
        foreground=[("selected", TEXT), ("active", TEXT)],
    )

    # Entry / Combobox
    style.configure(
        "TEntry", fieldbackground=SURFACE_HI, foreground=TEXT, insertcolor=TEXT,
        bordercolor=BORDER, borderwidth=1, padding=6, relief="flat",
    )
    style.map("TEntry", bordercolor=[("focus", ACCENT)])

    style.configure(
        "TCombobox", fieldbackground=SURFACE_HI, background=SURFACE_HI, foreground=TEXT,
        arrowcolor=MUTED, bordercolor=BORDER, borderwidth=1, padding=5, relief="flat",
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE_HI)],
        bordercolor=[("focus", ACCENT)],
        arrowcolor=[("active", TEXT)],
    )

    # LabelFrame → card with an accent title
    style.configure(
        "TLabelframe", background=BG, bordercolor=BORDER, borderwidth=1, relief="solid",
    )
    style.configure(
        "TLabelframe.Label", background=BG, foreground=ACCENT, font=FONT_BOLD,
    )

    # Checkbutton
    style.configure(
        "TCheckbutton", background=BG, foreground=TEXT, font=FONT,
        focuscolor=BG, indicatorrelief="flat",
    )
    style.map(
        "TCheckbutton",
        indicatorcolor=[("selected", ACCENT), ("!selected", SURFACE_HI), ("active", BORDER)],
        foreground=[("disabled", MUTED)],
    )

    # Scrollbar — slim, flat, no arrow buttons
    style.configure(
        "Vertical.TScrollbar", background=SURFACE_HI, troughcolor=BG, bordercolor=BG,
        arrowcolor=MUTED, borderwidth=0, relief="flat", width=12, arrowsize=12,
    )
    style.map("Vertical.TScrollbar", background=[("active", BORDER)])

    return {
        "bg": BG, "surface": SURFACE, "surface_hi": SURFACE_HI, "border": BORDER,
        "text": TEXT, "muted": MUTED, "accent": ACCENT, "danger": DANGER,
        "warn": WARN, "font_mono": FONT_MONO,
    }
