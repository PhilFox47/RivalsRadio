"""The Stage: a large, themed "now playing" view for a second monitor.

Reads everything from a shared ``StageState`` (so it always matches the web
overlay): the current hero's artwork on an accent-tinted background, the hero
name, an optional now-playing strip (album art + track + progress), and a live
audio-reactive visualizer. Switching heroes crossfades the background.

F11 toggles fullscreen, Esc leaves fullscreen.
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Optional, Tuple

from PIL import Image, ImageTk

from . import theming
from .config import Config
from .stage_render import render_background
from .stage_state import StageState
from .audio_visualizer import AudioVisualizer
from . import nowplaying

FPS_MS = 33                # ~30 fps animation tick
CROSSFADE_S = 0.45         # hero-change background crossfade duration


class StageWindow:
    def __init__(self, parent: tk.Misc, state: StageState, cfg: Config,
                 visualizer: AudioVisualizer) -> None:
        self.state = state
        self.cfg = cfg
        self.visualizer = visualizer

        self.top = tk.Toplevel(parent)
        self.top.title("RivalsRadio — Stage")
        self.top.configure(bg="#05060a")
        self.top.geometry("960x600")
        self.top.minsize(480, 320)

        self.canvas = tk.Canvas(self.top, bg="#05060a", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        # Rendered hero/accent currently shown (to detect changes from state).
        self._shown_hero: Optional[str] = None
        self._shown_accent: Tuple[int, int, int] = theming.hex_to_rgb(theming.DEFAULT_ACCENT)

        self._bg_photo: Optional[ImageTk.PhotoImage] = None
        self._cur_bg: Optional[Image.Image] = None
        self._prev_bg: Optional[Image.Image] = None
        self._fade_start = 0.0

        self._art_photo: Optional[ImageTk.PhotoImage] = None
        self._art_url = ""
        self._orig_avatar_cache: dict = {}

        self._items: dict = {}     # named canvas items (text/progress/art)
        self._viz_items: list = []
        self._levels: list = []
        self._last_size: Tuple[int, int] = (0, 0)
        self._fullscreen = False
        self._closed = False

        self.top.bind("<Configure>", self._on_configure)
        self.top.bind("<F11>", self._toggle_fullscreen)
        self.top.bind("<Escape>", lambda e: self._set_fullscreen(False))
        self.top.protocol("WM_DELETE_WINDOW", self.close)

        self.visualizer.start()
        self._rebuild()
        self._animate()

    # ------------------------------------------------------------------ API
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
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
                self._rebuild()

    # ----------------------------------------------------------- rendering
    def _load_avatar(self, path: Optional[str]) -> Optional[Image.Image]:
        if not path:
            return None
        img = self._orig_avatar_cache.get(path)
        if img is None:
            try:
                img = Image.open(path).convert("RGBA")
            except Exception:
                return None
            self._orig_avatar_cache[path] = img
        return img

    def _render_bg(self, w: int, h: int) -> Image.Image:
        avatar = self._load_avatar(self.state.avatar_path)
        return render_background(w, h, self._shown_accent, avatar)

    def _rebuild(self, crossfade: bool = False) -> None:
        if self._closed:
            return
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        self._last_size = (w, h)
        self.canvas.delete("all")
        self._items.clear()

        new_bg = self._render_bg(w, h)
        if crossfade and self._cur_bg is not None:
            self._prev_bg = self._cur_bg.resize((w, h)) if self._cur_bg.size != (w, h) else self._cur_bg
            self._fade_start = time.time()
        self._cur_bg = new_bg
        self._bg_photo = ImageTk.PhotoImage(new_bg)
        self._items["bg"] = self.canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)

        # Hero name.
        name = self._shown_hero or "Waiting for hero…"
        name_size = max(20, int(h * 0.075))
        self._text("name_sh", w // 2 + 2, int(h * 0.10) + 2, name, name_size, "#000000", bold=True)
        self._text("name", w // 2, int(h * 0.10), name, name_size, "#ffffff", bold=True)

        if self.cfg.show_now_playing:
            self._build_now_playing(w, h)
        self._build_visualizer(w, h)

    def _text(self, key: str, x: int, y: int, text: str, size: int, fill: str,
              bold: bool = False, anchor: str = "n") -> None:
        font = ("Segoe UI", size, "bold" if bold else "normal")
        self._items[key] = self.canvas.create_text(
            x, y, text=text, fill=fill, font=font, anchor=anchor)

    def _build_now_playing(self, w: int, h: int) -> None:
        pad = int(h * 0.03)
        art = int(h * 0.13)
        y = h - pad - art
        # Album art placeholder box.
        self._items["art_box"] = self.canvas.create_rectangle(
            pad, y, pad + art, y + art, outline="", fill="#111418")
        self._items["art"] = self.canvas.create_image(pad, y, anchor="nw")
        tx = pad + art + int(w * 0.012)
        ts = max(11, int(h * 0.030))
        self._text("track_sh", tx + 1, y + 1, "", ts, "#000000", bold=True, anchor="nw")
        self._text("track", tx, y, "", ts, "#ffffff", bold=True, anchor="nw")
        self._text("artist", tx, y + int(ts * 1.5), "", max(9, int(h * 0.022)),
                   "#c9c9c9", anchor="nw")
        # Progress bar.
        pb_y = y + art - max(4, int(h * 0.012))
        pb_w = int(w * 0.32)
        self._items["pb_bg"] = self.canvas.create_rectangle(
            tx, pb_y, tx + pb_w, pb_y + max(3, int(h * 0.008)),
            outline="", fill="#2a2e33")
        self._items["pb_fg"] = self.canvas.create_rectangle(
            tx, pb_y, tx, pb_y + max(3, int(h * 0.008)), outline="", fill="#ffffff")
        self._pb_geom = (tx, pb_y, pb_w, max(3, int(h * 0.008)))

    def _build_visualizer(self, w: int, h: int) -> None:
        for item in self._viz_items:
            self.canvas.delete(item)
        self._viz_items = []
        n = self.visualizer.bands
        self._levels = [0.0] * n
        fill = theming.rgb_to_hex(self._shown_accent)
        style = self.cfg.stage_style
        if style == "radial":
            cx, cy = w // 2, int(h * 0.52)
            for _ in range(n):
                self._viz_items.append(self.canvas.create_line(
                    cx, cy, cx, cy, fill=fill, width=max(1, int(w / n * 0.4))))
        else:
            margin = int(w * 0.04)
            usable = w - 2 * margin
            gap = max(1, int(usable / n * 0.25))
            bar_w = max(1, (usable - gap * (n - 1)) // n)
            base_y = int(h * 0.97) if style == "bars" else int(h * 0.70)
            for i in range(n):
                x0 = margin + i * (bar_w + gap)
                self._viz_items.append(self.canvas.create_rectangle(
                    x0, base_y, x0 + bar_w, base_y, fill=fill, width=0))
            self._viz_geom = (margin, gap, bar_w, base_y)

    # ----------------------------------------------------------- animation
    def _sync_state(self) -> None:
        """Pull hero/accent from shared state; crossfade on change."""
        hero = self.state.hero
        accent = theming.hex_to_rgb(self.state.accent_hex)
        if hero != self._shown_hero or accent != self._shown_accent:
            self._shown_hero = hero
            self._shown_accent = accent
            self._rebuild(crossfade=True)

    def _animate(self) -> None:
        if self._closed:
            return
        self._sync_state()
        self._update_crossfade()
        if self.cfg.show_now_playing:
            self._update_now_playing()
        self._update_visualizer()
        self.top.after(FPS_MS, self._animate)

    def _update_crossfade(self) -> None:
        if self._prev_bg is None:
            return
        t = (time.time() - self._fade_start) / CROSSFADE_S
        if t >= 1.0:
            self._prev_bg = None
            self._bg_photo = ImageTk.PhotoImage(self._cur_bg)
            self.canvas.itemconfig(self._items["bg"], image=self._bg_photo)
            return
        blended = Image.blend(self._prev_bg, self._cur_bg, t)
        self._bg_photo = ImageTk.PhotoImage(blended)
        self.canvas.itemconfig(self._items["bg"], image=self._bg_photo)

    def _update_now_playing(self) -> None:
        tr = self.state.track
        title = tr.title or "—"
        self.canvas.itemconfig(self._items["track"], text=title)
        self.canvas.itemconfig(self._items["track_sh"], text=title)
        self.canvas.itemconfig(self._items["artist"], text=tr.artist)
        # Album art (reload only when the URL changes).
        if tr.album_art_url and tr.album_art_url != self._art_url:
            path = nowplaying.art_path_for(tr.album_art_url)
            if path:
                try:
                    art = int(self._last_size[1] * 0.13)
                    img = Image.open(path).convert("RGB").resize((art, art), Image.LANCZOS)
                    self._art_photo = ImageTk.PhotoImage(img)
                    self.canvas.itemconfig(self._items["art"], image=self._art_photo)
                    self._art_url = tr.album_art_url
                except Exception:
                    pass
        # Progress bar.
        tx, pb_y, pb_w, pb_h = self._pb_geom
        frac = (tr.live_progress_ms() / tr.duration_ms) if tr.duration_ms else 0.0
        frac = max(0.0, min(1.0, frac))
        self.canvas.coords(self._items["pb_fg"], tx, pb_y, tx + pb_w * frac, pb_y + pb_h)

    def _update_visualizer(self) -> None:
        if not self._viz_items:
            return
        spectrum = self.visualizer.get_spectrum()
        w, h = self._last_size
        accent = self._shown_accent
        base_fill = theming.rgb_to_hex(accent)
        cap = theming.rgb_to_hex(theming.scale(accent, 1.4))
        style = self.cfg.stage_style
        n = len(self._viz_items)
        for i in range(n):
            target = float(spectrum[i]) if i < len(spectrum) else 0.0
            target = max(target, 0.03)
            self._levels[i] += (target - self._levels[i]) * 0.5
            lvl = self._levels[i]
            color = cap if lvl > 0.6 else base_fill
            if style == "radial":
                self._update_radial(i, n, lvl, w, h, color)
            elif style == "mirror":
                self._update_mirror(i, lvl, h, color)
            else:
                self._update_bars(i, lvl, h, color)

    def _update_bars(self, i, lvl, h, color) -> None:
        margin, gap, bar_w, base_y = self._viz_geom
        x0 = margin + i * (bar_w + gap)
        bh = 2 + lvl * max(8, int(h * 0.32))
        self.canvas.coords(self._viz_items[i], x0, base_y - bh, x0 + bar_w, base_y)
        self.canvas.itemconfig(self._viz_items[i], fill=color)

    def _update_mirror(self, i, lvl, h, color) -> None:
        margin, gap, bar_w, base_y = self._viz_geom
        x0 = margin + i * (bar_w + gap)
        bh = 2 + lvl * max(8, int(h * 0.22))
        self.canvas.coords(self._viz_items[i], x0, base_y - bh, x0 + bar_w, base_y + bh)
        self.canvas.itemconfig(self._viz_items[i], fill=color)

    def _update_radial(self, i, n, lvl, w, h, color) -> None:
        import math
        cx, cy = w // 2, int(h * 0.52)
        inner = min(w, h) * 0.30
        length = lvl * min(w, h) * 0.16
        ang = (i / n) * 2 * math.pi - math.pi / 2
        x0 = cx + math.cos(ang) * inner
        y0 = cy + math.sin(ang) * inner
        x1 = cx + math.cos(ang) * (inner + length)
        y1 = cy + math.sin(ang) * (inner + length)
        self.canvas.coords(self._viz_items[i], x0, y0, x1, y1)
        self.canvas.itemconfig(self._viz_items[i], fill=color)
