"""The Stage: a large, themed "now playing" view for a second monitor.

Shows the current hero's artwork on an accent-tinted background, the hero and
playlist names, and a live audio-reactive visualizer along the bottom. Press
F11 to toggle fullscreen, Esc to leave fullscreen.

Everything is drawn on a single Canvas: a static background layer (gradient +
glow + avatar, rebuilt only on hero change or resize) with text and animated
visualizer bars on top.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional, Tuple

from PIL import Image, ImageTk

from . import theming
from .stage_render import render_background
from .audio_visualizer import AudioVisualizer

FPS_MS = 33  # ~30 fps animation tick


class StageWindow:
    def __init__(self, parent: tk.Misc, visualizer: AudioVisualizer) -> None:
        self.visualizer = visualizer

        self.top = tk.Toplevel(parent)
        self.top.title("RivalsRadio — Stage")
        self.top.configure(bg="#05060a")
        self.top.geometry("960x600")
        self.top.minsize(480, 320)

        self.canvas = tk.Canvas(self.top, bg="#05060a", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.hero: Optional[str] = None
        self.playlist_name: str = ""
        self.accent: Tuple[int, int, int] = theming.hex_to_rgb(theming.DEFAULT_ACCENT)
        self._avatar_path: Optional[str] = None

        self._bg_photo: Optional[ImageTk.PhotoImage] = None
        self._orig_avatar_cache: dict = {}   # path -> original RGBA Image
        self._bars: list = []
        self._levels: list = []
        self._last_size: Tuple[int, int] = (0, 0)
        self._fullscreen = False
        self._closed = False

        self.top.bind("<Configure>", self._on_configure)
        self.top.bind("<F11>", self._toggle_fullscreen)
        self.top.bind("<Escape>", lambda e: self._set_fullscreen(False))
        self.top.protocol("WM_DELETE_WINDOW", self.close)

        self.visualizer.start()
        self._rebuild_static()
        self._animate()

    # ------------------------------------------------------------------ API
    def set_hero(self, hero: str, avatar_path: Optional[str], accent_hex: str,
                 playlist_name: str = "") -> None:
        self.hero = hero
        self._avatar_path = avatar_path
        self.playlist_name = playlist_name
        if accent_hex and theming.is_valid_hex(accent_hex):
            self.accent = theming.hex_to_rgb(accent_hex)
        self._rebuild_static()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.visualizer.stop()
        try:
            self.top.destroy()
        except tk.TclError:
            pass

    @property
    def alive(self) -> bool:
        return not self._closed and bool(self.top.winfo_exists())

    # --------------------------------------------------------------- window
    def _toggle_fullscreen(self, _event=None) -> None:
        self._set_fullscreen(not self._fullscreen)

    def _set_fullscreen(self, value: bool) -> None:
        self._fullscreen = value
        try:
            self.top.attributes("-fullscreen", value)
        except tk.TclError:
            pass

    def _on_configure(self, event) -> None:
        if event.widget is self.top:
            size = (self.canvas.winfo_width(), self.canvas.winfo_height())
            if size != self._last_size and size[0] > 1 and size[1] > 1:
                self._rebuild_static()

    # ----------------------------------------------------------- rendering
    def _load_avatar(self, path: str) -> Optional[Image.Image]:
        img = self._orig_avatar_cache.get(path)
        if img is None:
            try:
                img = Image.open(path).convert("RGBA")
            except Exception:
                return None
            self._orig_avatar_cache[path] = img
        return img

    def _rebuild_static(self) -> None:
        if self._closed:
            return
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        self._last_size = (w, h)
        self.canvas.delete("all")

        accent = self.accent
        avatar = self._load_avatar(self._avatar_path) if self._avatar_path else None
        bg = render_background(w, h, accent, avatar)

        self._bg_photo = ImageTk.PhotoImage(bg)
        self.canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)

        # Text: hero name + playlist, with a drop shadow for legibility.
        accent_hex = theming.rgb_to_hex(theming.scale(accent, 1.25))
        name = self.hero or "Waiting for hero…"
        name_size = max(20, int(h * 0.075))
        sub_size = max(11, int(h * 0.028))
        self._text(w // 2, int(h * 0.11), name, name_size, "#ffffff", bold=True)
        if self.playlist_name:
            self._text(w // 2, int(h * 0.11) + name_size,
                       self.playlist_name, sub_size, accent_hex)

        self._build_bars(w, h)

    def _text(self, x: int, y: int, text: str, size: int, fill: str,
              bold: bool = False) -> None:
        font = ("Segoe UI", size, "bold" if bold else "normal")
        self.canvas.create_text(x + 2, y + 2, text=text, fill="#000000",
                                 font=font, anchor="n")
        self.canvas.create_text(x, y, text=text, fill=fill, font=font, anchor="n")

    def _build_bars(self, w: int, h: int) -> None:
        n = self.visualizer.bands
        self._bars = []
        self._levels = [0.0] * n
        margin = int(w * 0.04)
        usable = w - 2 * margin
        gap = max(1, int(usable / n * 0.25))
        bar_w = max(1, (usable - gap * (n - 1)) // n)
        base_y = int(h * 0.97)
        fill = theming.rgb_to_hex(self.accent)
        for i in range(n):
            x0 = margin + i * (bar_w + gap)
            rect = self.canvas.create_rectangle(
                x0, base_y - 2, x0 + bar_w, base_y, fill=fill, width=0
            )
            self._bars.append((rect, x0, bar_w, base_y))

    # ----------------------------------------------------------- animation
    def _animate(self) -> None:
        if self._closed:
            return
        if self._bars:
            spectrum = self.visualizer.get_spectrum()
            n = len(self._bars)
            _, h = self._last_size
            max_h = max(8, int(h * 0.32))
            cap = theming.rgb_to_hex(theming.scale(self.accent, 1.4))
            base_fill = theming.rgb_to_hex(self.accent)
            for i, (rect, x0, bar_w, base_y) in enumerate(self._bars):
                target = float(spectrum[i]) if i < len(spectrum) else 0.0
                # Idle shimmer so it never looks dead when silent.
                target = max(target, 0.03)
                self._levels[i] += (target - self._levels[i]) * 0.5
                bh = 2 + self._levels[i] * max_h
                self.canvas.coords(rect, x0, base_y - bh, x0 + bar_w, base_y)
                self.canvas.itemconfig(
                    rect, fill=cap if self._levels[i] > 0.6 else base_fill
                )
        self.top.after(FPS_MS, self._animate)


