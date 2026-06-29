"""The Stage: a performant second-screen "now playing" view.

Design goal: simple, appealing, and above all **smooth**. The trick to high FPS
with Tkinter's canvas is to never allocate in the animation loop — so all the
expensive work (background render, the logo's scale ladder, album art) happens
**once** per hero/size change, and the per-frame loop only:

  • swaps the logo to a pre-rendered, pre-scaled PhotoImage (an index lookup),
  • moves the visualizer bar rectangles (canvas ``coords`` only),
  • nudges the progress bar.

No per-frame PIL work and no per-frame PhotoImage allocation, so it sustains the
configured frame rate (up to 160 FPS).

Layout:
  • centre     — hero logo, pulsing with the audio
  • top-right  — hero signature
  • top-left   — now playing (album art + track + progress)
  • bottom     — audio-reactive visualizer bars

F11 / double-click toggles fullscreen (on the window's current monitor),
Esc leaves fullscreen.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import List, Optional, Tuple

from PIL import Image, ImageChops, ImageTk

from . import theming
from .config import Config
from .stage_render import render_background
from .stage_state import StageState
from .audio_visualizer import AudioVisualizer
from . import nowplaying

PULSE_STEPS = 16           # pre-rendered logo scales (1.0 → PULSE_MAX)
PULSE_MAX = 1.16           # biggest logo scale at peak audio
FADE_STEPS = 7             # pre-rendered background crossfade frames
FADE_MS = 28               # ms per crossfade frame (~200ms total)


class StageWindow:
    def __init__(self, parent: tk.Misc, state: StageState, cfg: Config,
                 visualizer: AudioVisualizer) -> None:
        self.state = state
        self.cfg = cfg
        self.visualizer = visualizer

        # Animation cadence: drive the bars at the configured FPS (up to 160).
        self._fps = max(30, min(160, int(getattr(cfg, "stage_fps", 144))))
        self._frame_ms = max(6, int(round(1000.0 / self._fps)))

        self.top = tk.Toplevel(parent)
        self.top.title("RivalsRadio — Stage")
        self.top.configure(bg="#05060a")
        self.top.geometry("960x600")
        self.top.minsize(480, 320)

        self.canvas = tk.Canvas(self.top, bg="#05060a", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        # What's currently shown (so we only rebuild on real changes).
        self._shown_hero: Optional[str] = None
        self._shown_accent = theming.hex_to_rgb(theming.DEFAULT_ACCENT)
        self._shown_main = theming.hex_to_rgb(self.state.main_hex)
        self._shown_logo: Optional[str] = None
        self._shown_sig: Optional[str] = None

        # Cached, pre-rendered assets (rebuilt only on size/hero change).
        self._img_cache: dict = {}                 # path -> original RGBA Image
        self._bg_img: Optional[Image.Image] = None
        self._bg_photo: Optional[ImageTk.PhotoImage] = None
        self._logo_ladder: List[ImageTk.PhotoImage] = []
        self._sig_photo: Optional[ImageTk.PhotoImage] = None
        self._art_photo: Optional[ImageTk.PhotoImage] = None
        self._art_url = ""

        # Crossfade state (frames pre-rendered once per hero switch).
        self._fade_frames: List[ImageTk.PhotoImage] = []
        self._fade_after: Optional[str] = None

        # Per-frame live state.
        self._pulse = 0.0
        self._levels: List[float] = []
        self._items: dict = {}
        self._viz_items: List[int] = []
        self._viz_geom: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._pb_geom: Tuple[int, int, int, int] = (0, 0, 0, 0)
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
            hmon = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)  # NEAREST

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
        self._resize_after = self.top.after(120, self._rebuild)

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
        return img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                          Image.LANCZOS)

    # ----------------------------------------------------------- rebuild
    def _rebuild(self, crossfade: bool = False) -> None:
        if self._closed:
            return
        self._resize_after = None
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        prev_img = self._bg_img if crossfade else None
        self._last_size = (w, h)
        self._cancel_fade()
        self.canvas.delete("all")
        self._items.clear()

        # Background gradient + glow use the accent colour (the main colour now
        # tints the logo instead). Rendered once here.
        self._bg_img = render_background(w, h, self._shown_accent, None,
                                         main=self._shown_accent)
        self._bg_photo = ImageTk.PhotoImage(self._bg_img)
        self._items["bg"] = self.canvas.create_image(0, 0, anchor="nw",
                                                      image=self._bg_photo)

        self._build_logo(w, h)
        self._build_signature(w, h)
        if self.cfg.show_now_playing:
            self._build_now_playing(w, h)
        self._build_visualizer(w, h)

        self.canvas.create_text(
            w - 12, h - 8, text="Double-click / F11 fullscreen · Esc exit",
            fill="#6b7178", anchor="se",
            font=("Segoe UI", max(9, int(h * 0.013))))

        if prev_img is not None and prev_img.size == (w, h):
            self._start_fade(prev_img, self._bg_img)

    def _tint_logo(self, img: Image.Image) -> Image.Image:
        """Recolour a white-on-transparent logo with the hero's main colour.

        Multiplies the RGB by the main colour (so pure white → main colour, and
        any internal shading is preserved as darker shades) while keeping the
        original alpha, so the silhouette/edges stay intact."""
        solid = Image.new("RGB", img.size, self._shown_main)
        tinted = ImageChops.multiply(img.convert("RGB"), solid).convert("RGBA")
        tinted.putalpha(img.getchannel("A"))
        return tinted

    def _build_logo(self, w: int, h: int) -> None:
        """Pre-render a ladder of scaled, main-colour-tinted logo images so the
        pulse is just an index lookup at runtime (no per-frame PIL work)."""
        self._logo_ladder = []
        cx, cy = w // 2, int(h * 0.46)
        orig = self._load_image(self._shown_logo)
        if orig is not None:
            base = self._tint_logo(self._fit(orig, int(w * 0.46), int(h * 0.46)))
            for i in range(PULSE_STEPS):
                scale = 1.0 + (PULSE_MAX - 1.0) * (i / (PULSE_STEPS - 1))
                sw, sh = max(1, int(base.width * scale)), max(1, int(base.height * scale))
                frame = base if i == 0 else base.resize((sw, sh), Image.BILINEAR)
                self._logo_ladder.append(ImageTk.PhotoImage(frame))
            self._items["logo"] = self.canvas.create_image(
                cx, cy, image=self._logo_ladder[0])
        else:
            name = self._shown_hero or "Waiting for hero…"
            size = max(24, int(h * 0.11))
            tint = theming.rgb_to_hex(self._shown_main)
            self.canvas.create_text(cx + 2, cy + 2, text=name, fill="#000000",
                                    font=("Segoe UI", size, "bold"))
            self._items["logo_txt"] = self.canvas.create_text(
                cx, cy, text=name, fill=tint, font=("Segoe UI", size, "bold"))

    def _build_signature(self, w: int, h: int) -> None:
        sig = self._load_image(self._shown_sig)
        if sig is None:
            self._sig_photo = None
            return
        pad = int(h * 0.04)
        fitted = self._fit(sig, int(w * 0.32), int(h * 0.18))
        self._sig_photo = ImageTk.PhotoImage(fitted)
        self.canvas.create_image(w - pad, pad, image=self._sig_photo, anchor="ne")

    def _build_now_playing(self, w: int, h: int) -> None:
        pad = int(h * 0.045)
        art = int(h * 0.13)
        x, y = pad, pad
        self.canvas.create_rectangle(x, y, x + art, y + art, outline="", fill="#111418")
        self._items["art"] = self.canvas.create_image(x, y, anchor="nw")
        tx = x + art + int(w * 0.012)
        ts = max(12, int(h * 0.030))
        self._items["track_sh"] = self.canvas.create_text(
            tx + 1, y + 1, text="", fill="#000000", anchor="nw",
            font=("Segoe UI", ts, "bold"))
        self._items["track"] = self.canvas.create_text(
            tx, y, text="", fill="#ffffff", anchor="nw", font=("Segoe UI", ts, "bold"))
        self._items["artist"] = self.canvas.create_text(
            tx, y + int(ts * 1.5), text="", fill="#c9c9c9", anchor="nw",
            font=("Segoe UI", max(10, int(h * 0.022))))
        pb_y = y + art - max(4, int(h * 0.012))
        pb_w = int(w * 0.30)
        pb_h = max(3, int(h * 0.008))
        self.canvas.create_rectangle(tx, pb_y, tx + pb_w, pb_y + pb_h,
                                     outline="", fill="#2a2e33")
        self._items["pb_fg"] = self.canvas.create_rectangle(
            tx, pb_y, tx, pb_y + pb_h, outline="", fill="#ffffff")
        self._pb_geom = (tx, pb_y, pb_w, pb_h)

    def _build_visualizer(self, w: int, h: int) -> None:
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
            # Single fill set once; the loop only moves coords (no itemconfig).
            self._viz_items.append(self.canvas.create_rectangle(
                x0, base_y - 2, x0 + bar_w, base_y, fill=fill, width=0))
        self._viz_geom = (margin, gap, bar_w, base_y)

    # ----------------------------------------------------------- crossfade
    def _cancel_fade(self) -> None:
        if self._fade_after is not None:
            try:
                self.top.after_cancel(self._fade_after)
            except Exception:
                pass
            self._fade_after = None
        self._fade_frames = []

    def _start_fade(self, old: Image.Image, new: Image.Image) -> None:
        """Pre-render a few blended frames once, then step through them. Cheap:
        the blends happen here (once per hero switch), not in the animation loop."""
        try:
            self._fade_frames = [
                ImageTk.PhotoImage(Image.blend(old, new, (i + 1) / FADE_STEPS))
                for i in range(FADE_STEPS)
            ]
        except Exception:
            self._fade_frames = []
            return
        self._step_fade(0)

    def _step_fade(self, i: int) -> None:
        if self._closed or i >= len(self._fade_frames):
            self._fade_frames = []
            self._fade_after = None
            return
        self.canvas.itemconfig(self._items["bg"], image=self._fade_frames[i])
        self._fade_after = self.top.after(FADE_MS, lambda: self._step_fade(i + 1))

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
        self._update_logo_pulse()
        if self.cfg.show_now_playing:
            self._update_now_playing()
        self._update_visualizer()
        self.top.after(self._frame_ms, self._animate)

    def _audio_level(self) -> float:
        # The logo pulses to the beat (bass onset), not sustained volume.
        try:
            return float(self.visualizer.get_beat())
        except Exception:
            return 0.0

    def _update_logo_pulse(self) -> None:
        if not self._logo_ladder or "logo" not in self._items:
            return
        target = min(1.0, self._audio_level())
        # Punchy attack on a hit, smooth release so it eases back between beats.
        attack = target > self._pulse
        self._pulse += (target - self._pulse) * (0.6 if attack else 0.18)
        idx = int(self._pulse * (PULSE_STEPS - 1))
        idx = 0 if idx < 0 else (PULSE_STEPS - 1 if idx >= PULSE_STEPS else idx)
        self.canvas.itemconfig(self._items["logo"], image=self._logo_ladder[idx])

    def _update_now_playing(self) -> None:
        if "track" not in self._items:
            return
        tr = self.state.track
        title = tr.title or "—"
        # Text reconfig is cheap, but skip it when unchanged to stay allocation-free.
        if getattr(self, "_last_title", None) != title:
            self._last_title = title
            self.canvas.itemconfig(self._items["track"], text=title)
            self.canvas.itemconfig(self._items["track_sh"], text=title)
        if getattr(self, "_last_artist", None) != tr.artist:
            self._last_artist = tr.artist
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
        frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        self.canvas.coords(self._items["pb_fg"], tx, pb_y, tx + pb_w * frac, pb_y + pb_h)

    def _update_visualizer(self) -> None:
        if not self._viz_items:
            return
        spectrum = self.visualizer.get_spectrum()
        ns = len(spectrum)
        margin, gap, bar_w, base_y = self._viz_geom
        max_h = max(8, int(self._last_size[1] * 0.32))
        # Frame-rate-aware smoothing so high FPS glides instead of jittering.
        alpha = max(0.12, min(0.6, 0.5 * (60.0 / self._fps)))
        levels = self._levels
        coords = self.canvas.coords
        items = self._viz_items
        for i in range(len(items)):
            target = float(spectrum[i]) if i < ns else 0.0
            if target < 0.03:
                target = 0.03
            levels[i] += (target - levels[i]) * alpha
            x0 = margin + i * (bar_w + gap)
            coords(items[i], x0, base_y - (2 + levels[i] * max_h), x0 + bar_w, base_y)
