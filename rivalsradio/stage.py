"""The Stage: a performant, animated second-screen "now playing" view.

Two things keep it smooth:

1. **No UI-thread stalls on a hero switch.** All the heavy per-switch image work
   (background render + blur, the white logo's colour tint and its pulse
   scale-ladder, the portrait) is done on a **background thread**. The UI thread
   only ever wraps the finished PIL images in ``PhotoImage`` (cheap) and moves
   canvas items, so the window never freezes while a hero changes.

2. **A hero-switch animation** masks the swap: the new hero's **portrait sweeps
   across** the Stage while the background crossfades to the new colours, so the
   change feels fluid instead of a 1–2 s hang.

The steady-state animation loop is allocation-free: the logo pulse is an index
into the pre-rendered ladder, the bars only move ``coords``.

Layout: centre = logo (pulses to the bass), top-right = signature,
top-left = now playing, bottom = visualizer bars.

F11 / double-click toggles fullscreen on the window's current monitor; Esc exits.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageTk

from . import theming
from .config import Config
from .stage_render import render_background
from .stage_state import StageState
from .audio_visualizer import AudioVisualizer
from . import nowplaying

PULSE_STEPS = 16           # pre-rendered logo scales (1.0 → PULSE_MAX)
PULSE_MAX = 1.16           # biggest logo scale at peak bass
FADE_STEPS = 8             # pre-rendered background crossfade frames
ANIM_S = 0.6               # hero-switch animation duration (seconds)
LADDER_PER_FRAME = 3       # how many ladder PhotoImages to wrap per UI frame


# --- pure-PIL helpers (safe to call off the main thread) -------------------
def _open_rgba(path: Optional[str]) -> Optional[Image.Image]:
    if not path:
        return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def _fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    r = min(max_w / img.width, max_h / img.height)
    return img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                      Image.LANCZOS)


def _tint(img: Image.Image, rgb: Tuple[int, int, int]) -> Image.Image:
    """Recolour a white-on-transparent logo: multiply RGB by the colour (white →
    colour, shading preserved) while keeping the original alpha."""
    solid = Image.new("RGB", img.size, rgb)
    out = ImageChops.multiply(img.convert("RGB"), solid).convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def _smoothstep(t: float) -> float:
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return t * t * (3.0 - 2.0 * t)


class StageWindow:
    def __init__(self, parent: tk.Misc, state: StageState, cfg: Config,
                 visualizer: AudioVisualizer) -> None:
        self.state = state
        self.cfg = cfg
        self.visualizer = visualizer

        self._fps = max(30, min(160, int(getattr(cfg, "stage_fps", 144))))
        self._frame_ms = max(6, int(round(1000.0 / self._fps)))

        self.top = tk.Toplevel(parent)
        self.top.title("RivalsRadio — Stage")
        self.top.configure(bg="#05060a")
        self.top.geometry("960x600")
        self.top.minsize(480, 320)

        self.canvas = tk.Canvas(self.top, bg="#05060a", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        # What's currently shown (so we only switch on real changes).
        self._shown_hero: Optional[str] = None
        self._shown_accent = theming.hex_to_rgb(theming.DEFAULT_ACCENT)
        self._shown_main = theming.hex_to_rgb(self.state.main_hex)
        self._shown_logo: Optional[str] = None
        self._shown_sig: Optional[str] = None
        self._shown_portrait: Optional[str] = None

        # Retained PhotoImages / PIL (canvas only holds weak refs).
        self._bg_img: Optional[Image.Image] = None
        self._bg_photo: Optional[ImageTk.PhotoImage] = None
        self._fade_frames: List[ImageTk.PhotoImage] = []
        self._fade_pil: List[Image.Image] = []        # not-yet-wrapped fade frames
        self._logo_ladder: List[ImageTk.PhotoImage] = []
        self._ladder_pil: List[Image.Image] = []     # not-yet-wrapped pulse frames
        self._sig_photo: Optional[ImageTk.PhotoImage] = None
        self._art_photo: Optional[ImageTk.PhotoImage] = None
        self._portrait_photo: Optional[ImageTk.PhotoImage] = None
        self._art_url = ""

        # Async switch plumbing (worker thread → UI thread).
        self._asset_q: "queue.Queue[Tuple[int, dict]]" = queue.Queue()
        self._switch_seq = 0
        self._anim: Optional[dict] = None

        # Per-frame live state.
        self._pulse = 0.0
        self._levels: List[float] = []
        self._items: Dict[str, int] = {}
        self._viz_items: List[int] = []
        self._viz_geom: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._pb_geom: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._last_size: Tuple[int, int] = (0, 0)
        self._resize_after: Optional[str] = None
        self._fullscreen = False
        self._closed = False

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.top.bind("<F11>", self._toggle_fullscreen)
        self.top.bind("<Double-Button-1>", self._toggle_fullscreen)
        self.top.bind("<Escape>", lambda e: self._set_fullscreen(False))
        self.top.protocol("WM_DELETE_WINDOW", self.close)

        self.visualizer.start()
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

    def _on_canvas_configure(self, event) -> None:
        w, h = event.width, event.height
        if w <= 1 or h <= 1 or (w, h) == self._last_size:
            return
        # First valid size (or after the canvas was cleared): render right away.
        if self._last_size == (0, 0) or "bg" not in self._items:
            self._rebuild()
            return
        if self._resize_after is not None:
            try:
                self.top.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.top.after(120, self._rebuild)

    # ----------------------------------------------------------- assets (PIL)
    def _render_assets(self, w: int, h: int, *, accent, main, logo, sig, portrait,
                       hero, prev_bg, want_fade: bool) -> dict:
        """Build all the per-state images. Pure PIL — no Tk — so it is safe to
        run on a worker thread for hero switches."""
        bg = render_background(w, h, accent, None, main=accent)
        fades: List[Image.Image] = []
        if want_fade and prev_bg is not None and prev_bg.size == (w, h):
            fades = [Image.blend(prev_bg, bg, (i + 1) / FADE_STEPS)
                     for i in range(FADE_STEPS)]

        ladder: List[Image.Image] = []
        orig = _open_rgba(logo)
        if orig is not None:
            base = _tint(_fit(orig, int(w * 0.46), int(h * 0.46)), main)
            for i in range(PULSE_STEPS):
                scale = 1.0 + (PULSE_MAX - 1.0) * (i / (PULSE_STEPS - 1))
                if i == 0:
                    ladder.append(base)
                else:
                    sw, sh = max(1, int(base.width * scale)), max(1, int(base.height * scale))
                    ladder.append(base.resize((sw, sh), Image.BILINEAR))

        sig_img = _open_rgba(sig)
        sig_fit = _fit(sig_img, int(w * 0.32), int(h * 0.18)) if sig_img is not None else None

        por = _open_rgba(portrait)
        por_fit = _fit(por, int(w * 0.42), int(h * 0.72)) if por is not None else None

        return {
            "size": (w, h), "bg": bg, "fades": fades, "ladder": ladder,
            "logo_name": None if ladder else (hero or "Waiting for hero…"),
            "sig": sig_fit, "portrait": por_fit,
        }

    # ----------------------------------------------------------- paint (Tk)
    def _rebuild(self, _crossfade: bool = False) -> None:
        """Synchronous (re)paint for the initial render and resizes."""
        if self._closed:
            return
        self._resize_after = None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        assets = self._render_assets(
            w, h, accent=self._shown_accent, main=self._shown_main,
            logo=self._shown_logo, sig=self._shown_sig, portrait=self._shown_portrait,
            hero=self._shown_hero, prev_bg=None, want_fade=False)
        self._paint(assets, animate=False)

    def _paint(self, assets: dict, animate: bool) -> None:
        if self._closed:
            return
        self._cancel_anim()
        w, h = assets["size"]
        self._last_size = (w, h)
        self.canvas.delete("all")
        self._items.clear()

        # Background. Wrap the new bg + only the first crossfade frame now; wrap
        # the remaining (full-screen) fade frames lazily so we don't allocate a
        # stack of large PhotoImages in a single UI frame.
        self._bg_img = assets["bg"]
        self._bg_photo = ImageTk.PhotoImage(self._bg_img)
        self._fade_frames = []
        self._fade_pil = []
        if animate and assets["fades"]:
            self._fade_frames = [ImageTk.PhotoImage(assets["fades"][0])]
            self._fade_pil = list(assets["fades"][1:])
            first_bg = self._fade_frames[0]
        else:
            first_bg = self._bg_photo
        self._items["bg"] = self.canvas.create_image(0, 0, anchor="nw", image=first_bg)

        # Portrait sits just above the background, behind everything else, so it
        # sweeps in front of the glow but the logo stays the focal point.
        self._portrait_photo = None
        if animate and assets["portrait"] is not None:
            self._portrait_photo = ImageTk.PhotoImage(assets["portrait"])
            self._items["portrait"] = self.canvas.create_image(
                -10000, h // 2, image=self._portrait_photo)  # off-screen until anim

        self._build_logo(w, h, assets)
        self._build_signature(w, h, assets)
        if self.cfg.show_now_playing:
            self._build_now_playing(w, h)
        self._build_visualizer(w, h)
        self.canvas.create_text(
            w - 12, h - 8, text="Double-click / F11 fullscreen · Esc exit",
            fill="#6b7178", anchor="se", font=("Segoe UI", max(9, int(h * 0.013))))

        if animate:
            self._start_anim(w, h)

    def _build_logo(self, w: int, h: int, assets: dict) -> None:
        cx, cy = w // 2, int(h * 0.46)
        ladder = assets["ladder"]
        self._logo_ladder = []
        self._ladder_pil = []
        if ladder:
            # Wrap the base now; wrap the rest of the scale-ladder lazily over the
            # next few UI frames so we never block on ~16 PhotoImage creations.
            self._logo_ladder.append(ImageTk.PhotoImage(ladder[0]))
            self._ladder_pil = list(ladder[1:])
            self._items["logo"] = self.canvas.create_image(cx, cy, image=self._logo_ladder[0])
        else:
            name = assets["logo_name"] or "Waiting for hero…"
            size = max(24, int(h * 0.11))
            tint = theming.rgb_to_hex(self._shown_main)
            self.canvas.create_text(cx + 2, cy + 2, text=name, fill="#000000",
                                    font=("Segoe UI", size, "bold"))
            self._items["logo_txt"] = self.canvas.create_text(
                cx, cy, text=name, fill=tint, font=("Segoe UI", size, "bold"))

    def _build_signature(self, w: int, h: int, assets: dict) -> None:
        sig = assets["sig"]
        if sig is None:
            self._sig_photo = None
            return
        pad = int(h * 0.04)
        self._sig_photo = ImageTk.PhotoImage(sig)
        self.canvas.create_image(w - pad, pad, image=self._sig_photo, anchor="ne")

    def _build_now_playing(self, w: int, h: int) -> None:
        # Items are recreated empty; clear the "last shown" trackers so
        # _update_now_playing refills them on the next tick (otherwise they'd
        # stay blank after a rebuild, e.g. going fullscreen, until the next song).
        self._last_title = None
        self._last_artist = None
        self._art_url = ""
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
            self._viz_items.append(self.canvas.create_rectangle(
                x0, base_y - 2, x0 + bar_w, base_y, fill=fill, width=0))
        self._viz_geom = (margin, gap, bar_w, base_y)

    # ----------------------------------------------------------- switching
    def _start_switch(self) -> None:
        """Kick off a background render of the new hero's assets."""
        w, h = self._last_size
        if w <= 1 or h <= 1:
            return
        self._switch_seq += 1
        seq = self._switch_seq
        params = dict(
            w=w, h=h, accent=self._shown_accent, main=self._shown_main,
            logo=self._shown_logo, sig=self._shown_sig, portrait=self._shown_portrait,
            hero=self._shown_hero, prev_bg=self._bg_img)
        threading.Thread(target=self._switch_worker, args=(seq, params),
                         name="stage-switch", daemon=True).start()

    def _switch_worker(self, seq: int, p: dict) -> None:
        try:
            assets = self._render_assets(
                p["w"], p["h"], accent=p["accent"], main=p["main"], logo=p["logo"],
                sig=p["sig"], portrait=p["portrait"], hero=p["hero"],
                prev_bg=p["prev_bg"], want_fade=True)
        except Exception:
            return
        self._asset_q.put((seq, assets))

    def _poll_switch(self) -> None:
        applied = None
        try:
            while True:
                seq, assets = self._asset_q.get_nowait()
                if seq == self._switch_seq:
                    applied = assets
        except queue.Empty:
            pass
        if applied is not None and not self._closed:
            # Drop a result whose size no longer matches (window was resized
            # while it rendered) — the resize already repainted at the new size.
            if applied["size"] == self._last_size:
                self._paint(applied, animate=True)

    # ----------------------------------------------------------- animation
    def _cancel_anim(self) -> None:
        self._anim = None

    def _start_anim(self, w: int, h: int) -> None:
        a = {"t0": time.time(), "dur": ANIM_S}
        if "portrait" in self._items and self._portrait_photo is not None:
            pw = self._portrait_photo.width()
            a["px0"] = w + pw // 2 + 30        # off-screen right
            a["px1"] = -pw // 2 - 30           # off-screen left
            a["py"] = int(h * 0.5)
        self._anim = a

    def _step_anim(self) -> None:
        a = self._anim
        if a is None:
            return
        p = (time.time() - a["t0"]) / a["dur"]
        if p >= 1.0:
            # Settle the background on its retained final image, drop the portrait.
            if "bg" in self._items and self._bg_photo is not None:
                self.canvas.itemconfig(self._items["bg"], image=self._bg_photo)
            if "portrait" in self._items:
                self.canvas.delete(self._items.pop("portrait"))
            self._portrait_photo = None
            self._fade_frames = []
            self._anim = None
            return
        e = _smoothstep(p)
        if self._fade_frames:
            # Timeline spans all FADE_STEPS frames; clamp to those wrapped so far.
            idx = int(p * FADE_STEPS)
            built = len(self._fade_frames) - 1
            if idx > built:
                idx = built
            self.canvas.itemconfig(self._items["bg"], image=self._fade_frames[idx])
        if "portrait" in self._items and "px0" in a:
            x = a["px0"] + (a["px1"] - a["px0"]) * e
            self.canvas.coords(self._items["portrait"], x, a["py"])

    # ----------------------------------------------------------- loop
    def _sync_state(self) -> None:
        hero = self.state.hero
        accent = theming.hex_to_rgb(self.state.accent_hex)
        main = theming.hex_to_rgb(self.state.main_hex)
        logo = self.state.logo_path
        sig = self.state.signature_path
        portrait = self.state.portrait_path
        if (hero != self._shown_hero or accent != self._shown_accent
                or main != self._shown_main or logo != self._shown_logo
                or sig != self._shown_sig or portrait != self._shown_portrait):
            had_render = "bg" in self._items and self._last_size != (0, 0)
            self._shown_hero = hero
            self._shown_accent = accent
            self._shown_main = main
            self._shown_logo = logo
            self._shown_sig = sig
            self._shown_portrait = portrait
            if had_render:
                self._start_switch()        # animated, off-thread
            # else: the first paint happens via _on_canvas_configure → _rebuild

    def _wrap_pending(self) -> None:
        """Wrap a few queued fade/ladder frames into PhotoImages per UI frame so
        the work is spread out instead of stalling one frame."""
        n = LADDER_PER_FRAME
        while n > 0 and self._fade_pil:
            self._fade_frames.append(ImageTk.PhotoImage(self._fade_pil.pop(0)))
            n -= 1
        while n > 0 and self._ladder_pil:
            self._logo_ladder.append(ImageTk.PhotoImage(self._ladder_pil.pop(0)))
            n -= 1

    def _animate(self) -> None:
        if self._closed:
            return
        self._sync_state()
        self._poll_switch()
        if self._fade_pil or self._ladder_pil:
            self._wrap_pending()
        if self._anim is not None:
            self._step_anim()
        self._update_logo_pulse()
        if self.cfg.show_now_playing:
            self._update_now_playing()
        self._update_visualizer()
        self.top.after(self._frame_ms, self._animate)

    def _audio_level(self) -> float:
        try:
            return float(self.visualizer.get_beat())
        except Exception:
            return 0.0

    def _update_logo_pulse(self) -> None:
        if not self._logo_ladder or "logo" not in self._items:
            return
        target = min(1.0, self._audio_level())
        attack = target > self._pulse
        self._pulse += (target - self._pulse) * (0.6 if attack else 0.18)
        idx = int(self._pulse * (PULSE_STEPS - 1))
        top = len(self._logo_ladder) - 1
        idx = 0 if idx < 0 else (top if idx > top else idx)
        self.canvas.itemconfig(self._items["logo"], image=self._logo_ladder[idx])

    def _update_now_playing(self) -> None:
        if "track" not in self._items:
            return
        tr = self.state.track
        title = tr.title or "—"
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
