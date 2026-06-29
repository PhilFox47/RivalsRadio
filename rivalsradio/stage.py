"""The Stage: a large, themed "now playing" view for a second monitor.

Layout:
  • centre  — the hero's **logo**, pulsing with the audio
  • top-right — the hero's **signature**
  • top-left  — now-playing (album art + track + progress)
  • bottom    — the audio-reactive visualizer bars

Reads everything from a shared ``StageState`` so it always matches the web
overlay. Switching heroes crossfades the accent background.

F11 (or double-click) toggles fullscreen, Esc leaves fullscreen.
"""

from __future__ import annotations

import sys
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

CROSSFADE_S = 0.45         # hero-change background crossfade duration
LOGO_PULSE_MS = 33         # cap the (expensive) logo PIL resize to ~30 fps


class StageWindow:
    def __init__(self, parent: tk.Misc, state: StageState, cfg: Config,
                 visualizer: AudioVisualizer) -> None:
        self.state = state
        self.cfg = cfg
        self.visualizer = visualizer

        # Animation cadence: drive the bars at the configured FPS (up to 160).
        self._fps = max(30, min(160, int(getattr(cfg, "stage_fps", 144))))
        self._frame_ms = max(6, int(round(1000.0 / self._fps)))
        self._last_pulse = 0.0

        self.top = tk.Toplevel(parent)
        self.top.title("RivalsRadio — Stage")
        self.top.configure(bg="#05060a")
        self.top.geometry("960x600")
        self.top.minsize(480, 320)

        self.canvas = tk.Canvas(self.top, bg="#05060a", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._shown_hero: Optional[str] = None
        self._shown_accent: Tuple[int, int, int] = theming.hex_to_rgb(theming.DEFAULT_ACCENT)
        self._shown_main: Tuple[int, int, int] = theming.hex_to_rgb(self.state.main_hex)
        self._shown_logo: Optional[str] = None
        self._shown_sig: Optional[str] = None

        self._bg_photo: Optional[ImageTk.PhotoImage] = None
        self._cur_bg: Optional[Image.Image] = None
        self._prev_bg: Optional[Image.Image] = None
        self._fade_start = 0.0

        self._img_cache: dict = {}          # path -> original RGBA Image
        self._logo_orig: Optional[Image.Image] = None
        self._logo_photo: Optional[ImageTk.PhotoImage] = None
        self._logo_base_h = 1
        self._sig_photo: Optional[ImageTk.PhotoImage] = None
        self._art_photo: Optional[ImageTk.PhotoImage] = None
        self._art_url = ""
        self._pulse = 0.0

        self._items: dict = {}
        self._viz_items: list = []
        self._levels: list = []
        self._last_size: Tuple[int, int] = (0, 0)
        self._resize_after: Optional[str] = None
        self._fullscreen = False
        self._closed = False

        self.top.bind("<Configure>", self._on_configure)
        self.top.bind("<F11>", self._toggle_fullscreen)
        self.top.bind("<Double-Button-1>", self._toggle_fullscreen)
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

    def set_fps(self, fps: int) -> None:
        """Update the animation frame rate live (clamped to 30–160)."""
        self._fps = max(30, min(160, int(fps)))
        self._frame_ms = max(6, int(round(1000.0 / self._fps)))

    # --------------------------------------------------------------- window
    def _toggle_fullscreen(self, _event=None) -> None:
        self._set_fullscreen(not self._fullscreen)

    def _monitor_rect(self) -> Optional[Tuple[int, int, int, int]]:
        """Physical rect (x, y, w, h) of the monitor the window sits on (Win32)."""
        if not sys.platform.startswith("win"):
            return None
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = ctypes.windll.user32.GetParent(self.top.winfo_id())
            if not hwnd:
                hwnd = self.top.winfo_id()
            MONITOR_DEFAULTTONEAREST = 2
            hmon = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD),
                            ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD)]

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if not ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                return None
            r = mi.rcMonitor
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
        except Exception:
            return None

    def _set_fullscreen(self, value: bool) -> None:
        self._fullscreen = value
        rect = self._monitor_rect() if value else None
        if rect is not None:
            # Borderless fullscreen on the monitor the window is currently on —
            # avoids Tk's "-fullscreen" jumping to the primary monitor, and sets
            # an exact physical-pixel geometry so the canvas fills the screen.
            x, y, w, h = rect
            try:
                self.top.attributes("-fullscreen", False)
            except tk.TclError:
                pass
            self.top.overrideredirect(True)
            self.top.geometry(f"{w}x{h}+{x}+{y}")
            self.top.lift()
            self.top.after(30, self._rebuild)
            return
        if not value:
            try:
                self.top.overrideredirect(False)
            except tk.TclError:
                pass
        try:
            self.top.attributes("-fullscreen", value)
        except tk.TclError:
            pass

    def _on_configure(self, event) -> None:
        if event.widget is not self.top:
            return
        size = (self.canvas.winfo_width(), self.canvas.winfo_height())
        if size == self._last_size or size[0] <= 1 or size[1] <= 1:
            return
        if self._resize_after is not None:
            try:
                self.top.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.top.after(90, self._rebuild)

    # ----------------------------------------------------------- image load
    def _load_image(self, path: Optional[str]) -> Optional[Image.Image]:
        if not path:
            return None
        img = self._img_cache.get(path)
        if img is None:
            try:
                img = Image.open(path).convert("RGBA")
            except Exception:
                return None
            self._img_cache[path] = img
        return img

    @staticmethod
    def _fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
        r = min(max_w / img.width, max_h / img.height)
        return img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)

    # ----------------------------------------------------------- rendering
    def _rebuild(self, crossfade: bool = False) -> None:
        if self._closed:
            return
        self._resize_after = None
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        self._last_size = (w, h)
        self.canvas.delete("all")
        self._items.clear()

        # Background: main-colour gradient + glow (no portrait).
        new_bg = render_background(w, h, self._shown_accent, None, main=self._shown_main)
        if crossfade and self._cur_bg is not None:
            self._prev_bg = self._cur_bg.resize((w, h)) if self._cur_bg.size != (w, h) else self._cur_bg
            self._fade_start = time.time()
        self._cur_bg = new_bg
        self._bg_photo = ImageTk.PhotoImage(new_bg)
        self._items["bg"] = self.canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)

        self._build_logo(w, h)
        self._build_signature(w, h)
        if self.cfg.show_now_playing:
            self._build_now_playing(w, h)
        self._build_visualizer(w, h)

        # Discoverable fullscreen hint.
        self._text("hint", w - 12, h - 8, "Double-click / F11 fullscreen · Esc exit",
                   max(10, int(h * 0.014)), "#6b7178", anchor="se")

    def _text(self, key: str, x: int, y: int, text: str, size: int, fill: str,
              bold: bool = False, anchor: str = "n") -> None:
        font = ("Segoe UI", size, "bold" if bold else "normal")
        self._items[key] = self.canvas.create_text(
            x, y, text=text, fill=fill, font=font, anchor=anchor)

    def _build_logo(self, w: int, h: int) -> None:
        cx, cy = w // 2, int(h * 0.46)
        self._logo_orig = self._load_image(self._shown_logo)
        if self._logo_orig is not None:
            fitted = self._fit(self._logo_orig, int(w * 0.5), int(h * 0.5))
            self._logo_base_h = fitted.height
            self._logo_photo = ImageTk.PhotoImage(fitted)
            self._items["logo"] = self.canvas.create_image(cx, cy, image=self._logo_photo)
        else:
            # Fallback: the hero name, large and centred, if no logo is set.
            name = self._shown_hero or "Waiting for hero…"
            size = max(24, int(h * 0.11))
            self._text("logo_sh", cx + 2, cy + 2, name, size, "#000000", bold=True, anchor="c")
            self._text("logo", cx, cy, name, size, "#ffffff", bold=True, anchor="c")

    def _build_signature(self, w: int, h: int) -> None:
        sig = self._load_image(self._shown_sig)
        if sig is None:
            return
        pad = int(h * 0.04)
        fitted = self._fit(sig, int(w * 0.34), int(h * 0.20))
        self._sig_photo = ImageTk.PhotoImage(fitted)
        self._items["sig"] = self.canvas.create_image(w - pad, pad, image=self._sig_photo, anchor="ne")

    def _build_now_playing(self, w: int, h: int) -> None:
        pad = int(h * 0.045)
        art = int(h * 0.13)
        x, y = pad, pad
        self._items["art_box"] = self.canvas.create_rectangle(
            x, y, x + art, y + art, outline="", fill="#111418")
        self._items["art"] = self.canvas.create_image(x, y, anchor="nw")
        tx = x + art + int(w * 0.012)
        ts = max(12, int(h * 0.030))
        self._text("track_sh", tx + 1, y + 1, "", ts, "#000000", bold=True, anchor="nw")
        self._text("track", tx, y, "", ts, "#ffffff", bold=True, anchor="nw")
        self._text("artist", tx, y + int(ts * 1.5), "", max(10, int(h * 0.022)),
                   "#c9c9c9", anchor="nw")
        pb_y = y + art - max(4, int(h * 0.012))
        pb_w = int(w * 0.30)
        pb_h = max(3, int(h * 0.008))
        self._items["pb_bg"] = self.canvas.create_rectangle(
            tx, pb_y, tx + pb_w, pb_y + pb_h, outline="", fill="#2a2e33")
        self._items["pb_fg"] = self.canvas.create_rectangle(
            tx, pb_y, tx, pb_y + pb_h, outline="", fill="#ffffff")
        self._pb_geom = (tx, pb_y, pb_w, pb_h)

    def _build_visualizer(self, w: int, h: int) -> None:
        for item in self._viz_items:
            self.canvas.delete(item)
        self._viz_items = []
        n = self.visualizer.bands
        self._levels = [0.0] * n
        fill = theming.rgb_to_hex(self._shown_accent)
        margin = int(w * 0.04)
        usable = w - 2 * margin
        gap = max(1, int(usable / n * 0.25))
        bar_w = max(1, (usable - gap * (n - 1)) // n)
        base_y = int(h * 0.97)
        for i in range(n):
            x0 = margin + i * (bar_w + gap)
            self._viz_items.append(self.canvas.create_rectangle(
                x0, base_y, x0 + bar_w, base_y, fill=fill, width=0))
        self._viz_geom = (margin, gap, bar_w, base_y)

    # ----------------------------------------------------------- animation
    def _sync_state(self) -> None:
        hero = self.state.hero
        accent = theming.hex_to_rgb(self.state.accent_hex)
        main = theming.hex_to_rgb(self.state.main_hex)
        logo = self.state.logo_path
        sig = self.state.signature_path
        if (hero != self._shown_hero or accent != self._shown_accent
                or main != self._shown_main
                or logo != self._shown_logo or sig != self._shown_sig):
            self._shown_hero = hero
            self._shown_accent = accent
            self._shown_main = main
            self._shown_logo = logo
            self._shown_sig = sig
            self._rebuild(crossfade=True)

    def _animate(self) -> None:
        if self._closed:
            return
        self._sync_state()
        self._update_crossfade()
        # The logo's per-frame PIL resize is expensive; cap it well below the
        # bar frame rate so the bars can run smooth at up to 160 FPS.
        now = time.time()
        if (now - self._last_pulse) * 1000.0 >= LOGO_PULSE_MS:
            self._last_pulse = now
            self._update_logo_pulse()
        if self.cfg.show_now_playing:
            self._update_now_playing()
        self._update_visualizer()
        self.top.after(self._frame_ms, self._animate)

    def _audio_level(self) -> float:
        spectrum = self.visualizer.get_spectrum()
        if spectrum is None or len(spectrum) == 0:
            return 0.0
        try:
            return float(sum(spectrum) / len(spectrum))
        except Exception:
            return 0.0

    def _update_logo_pulse(self) -> None:
        if self._logo_orig is None or "logo" not in self._items:
            return
        target = self._audio_level()
        self._pulse += (target - self._pulse) * 0.4
        scale = 1.0 + min(0.22, self._pulse * 0.6)   # subtle, capped
        w, h = self._last_size
        base = self._fit(self._logo_orig, int(w * 0.5), int(h * 0.5))
        new_h = max(1, int(base.height * scale))
        new_w = max(1, int(base.width * scale))
        try:
            img = base.resize((new_w, new_h), Image.BILINEAR)
            self._logo_photo = ImageTk.PhotoImage(img)
            self.canvas.itemconfig(self._items["logo"], image=self._logo_photo)
        except Exception:
            pass

    def _update_crossfade(self) -> None:
        if self._prev_bg is None:
            return
        if self._prev_bg.size != self._cur_bg.size:   # e.g. a resize mid-fade
            try:
                self._prev_bg = self._prev_bg.resize(self._cur_bg.size)
            except Exception:
                self._prev_bg = None
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
        if "track" not in self._items:
            return
        tr = self.state.track
        title = tr.title or "—"
        self.canvas.itemconfig(self._items["track"], text=title)
        self.canvas.itemconfig(self._items["track_sh"], text=title)
        self.canvas.itemconfig(self._items["artist"], text=tr.artist)
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
        margin, gap, bar_w, base_y = self._viz_geom
        # Keep the visual response consistent regardless of frame rate: the
        # per-frame blend is scaled down as FPS rises so bars glide, not jitter.
        alpha = max(0.12, min(0.6, 0.5 * (60.0 / self._fps)))
        for i in range(len(self._viz_items)):
            target = float(spectrum[i]) if i < len(spectrum) else 0.0
            target = max(target, 0.03)
            self._levels[i] += (target - self._levels[i]) * alpha
            lvl = self._levels[i]
            color = cap if lvl > 0.6 else base_fill
            x0 = margin + i * (bar_w + gap)
            bh = 2 + lvl * max(8, int(h * 0.32))
            self.canvas.coords(self._viz_items[i], x0, base_y - bh, x0 + bar_w, base_y)
            self.canvas.itemconfig(self._viz_items[i], fill=color)
