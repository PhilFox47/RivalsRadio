"""The Stage: a performant, animated second-screen "now playing" view.

Why this is smooth (each item fixes a measured source of chop):

1. **1 ms timer resolution.** Windows quantizes Tk's ``after()`` to ~15.6 ms by
   default, turning a requested 144 FPS into an uneven ~64. The Stage calls
   ``timeBeginPeriod(1)`` while open so frames are actually delivered on time.

2. **Drift-corrected frame pacing.** The loop self-schedules against
   ``perf_counter`` (compute how late we are, sleep the remainder) instead of a
   fixed ``after(7)``, so frame intervals stay even instead of accumulating
   jitter.

3. **No UI-thread stall on a hero switch.** All heavy per-switch image work
   (background render + blur, logo tint + pulse scale-ladder, portrait) runs on
   a background thread (pure PIL releases the GIL). The results are then
   wrapped into ``PhotoImage``s **one step per frame** while the screen is
   covered — never several full-screen wraps in a single frame.

4. **A slanted cover wipe masks the swap.** On a hero change a slanted panel in
   the hero's colour (with an accent leading edge) sweeps in instantly, the
   Stage rebuilds behind it step-by-step, the new portrait holds with a subtle
   drift, then the panel sweeps off revealing the finished Stage. While the
   panel moves, the underlying animations pause so the panel is the only damage
   the canvas repaints — the sweep itself stays fluid even at 4K.

The steady-state loop is allocation-free: the logo pulse is an index into the
pre-rendered ladder; the bars only move ``coords``.

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
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageTk

from . import theming
from .config import Config
from .stage_render import render_background
from .stage_state import StageState
from .audio_visualizer import AudioVisualizer
from . import nowplaying

PULSE_STEPS = 16           # pre-rendered logo scales (1.0 → PULSE_MAX)
PULSE_MAX = 1.16           # biggest logo scale at peak bass
T_IN = 0.32                # cover sweep-in duration, seconds
T_SHOW = 0.55              # portrait hold while covered, seconds
T_OUT = 0.38               # cover sweep-out (reveal) duration, seconds
SLANT_FRAC = 0.10          # slant of the cover's edges, as a fraction of width
EDGE_FRAC = 0.008          # accent edge stripe width, as a fraction of width
LADDER_PER_FRAME = 3       # ladder PhotoImages wrapped per UI frame


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
        self._frame_s = 1.0 / self._fps
        self._next_frame = time.perf_counter()

        # Windows quantizes Tk timers to ~15.6ms without this; 1ms resolution is
        # what makes high-FPS animation via after() possible at all.
        self._timer_boosted = False
        if sys.platform.startswith("win"):
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
                self._timer_boosted = True
            except Exception:
                pass

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

        # Retained PhotoImages / PIL (canvas holds only weak refs).
        self._bg_img: Optional[Image.Image] = None
        self._bg_photo: Optional[ImageTk.PhotoImage] = None
        self._logo_ladder: List[ImageTk.PhotoImage] = []
        self._ladder_pil: List[Image.Image] = []
        self._sig_photo: Optional[ImageTk.PhotoImage] = None
        self._art_photo: Optional[ImageTk.PhotoImage] = None
        self._cover_photo: Optional[ImageTk.PhotoImage] = None
        self._art_url = ""

        # Async switch + transition plumbing.
        self._asset_q: "queue.Queue[Tuple[int, dict]]" = queue.Queue()
        self._switch_seq = 0
        self._anim: Optional[dict] = None
        self._swap_steps: List[Callable[[], None]] = []
        self._cover: Optional[dict] = None

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
        if self._timer_boosted:
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
        try:
            self.top.destroy()
        except tk.TclError:
            pass

    @property
    def alive(self) -> bool:
        return not self._closed and bool(self.top.winfo_exists())

    def set_fps(self, fps: int) -> None:
        self._fps = max(30, min(160, int(fps)))
        self._frame_s = 1.0 / self._fps

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
                       hero) -> dict:
        """Build all per-state images. Pure PIL — no Tk — safe off-thread."""
        bg = render_background(w, h, accent, None, main=accent)

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
        por_fit = _fit(por, int(w * 0.52), int(h * 0.84)) if por is not None else None

        return {
            "size": (w, h), "bg": bg, "ladder": ladder,
            "logo_name": None if ladder else (hero or "Waiting for hero…"),
            "sig": sig_fit, "portrait": por_fit,
        }

    # ----------------------------------------------------------- paint (Tk)
    def _rebuild(self, _crossfade: bool = False) -> None:
        """Synchronous (re)paint — initial render and resizes only."""
        if self._closed:
            return
        self._resize_after = None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        self._cancel_anim()
        assets = self._render_assets(
            w, h, accent=self._shown_accent, main=self._shown_main,
            logo=self._shown_logo, sig=self._shown_sig, portrait=self._shown_portrait,
            hero=self._shown_hero)
        self._bg_photo = ImageTk.PhotoImage(assets["bg"])
        self._sig_photo = (ImageTk.PhotoImage(assets["sig"])
                           if assets["sig"] is not None else None)
        self._paint_items(assets)

    def _paint_items(self, assets: dict) -> None:
        """Create every canvas item for `assets`. Assumes self._bg_photo (and
        self._sig_photo, if any) are already wrapped — so this call itself is
        cheap creates, no big allocations."""
        if self._closed:
            return
        w, h = assets["size"]
        self._last_size = (w, h)
        self._bg_img = assets["bg"]
        self.canvas.delete("all")
        self._items.clear()
        self._cover = None   # delete("all") removed it; caller recreates if needed

        self._items["bg"] = self.canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
        self._build_logo(w, h, assets)
        if self._sig_photo is not None:
            pad = int(h * 0.04)
            self.canvas.create_image(w - pad, pad, image=self._sig_photo, anchor="ne")
        if self.cfg.show_now_playing:
            self._build_now_playing(w, h)
        self._build_visualizer(w, h)
        self.canvas.create_text(
            w - 12, h - 8, text="Double-click / F11 fullscreen · Esc exit",
            fill="#6b7178", anchor="se", font=("Segoe UI", max(9, int(h * 0.013))))

    def _build_logo(self, w: int, h: int, assets: dict) -> None:
        cx, cy = w // 2, int(h * 0.46)
        ladder = assets["ladder"]
        self._logo_ladder = []
        self._ladder_pil = []
        if ladder:
            self._logo_ladder.append(ImageTk.PhotoImage(ladder[0]))
            self._ladder_pil = list(ladder[1:])   # wrapped lazily by the loop
            self._items["logo"] = self.canvas.create_image(cx, cy, image=self._logo_ladder[0])
        else:
            name = assets["logo_name"] or "Waiting for hero…"
            size = max(24, int(h * 0.11))
            tint = theming.rgb_to_hex(self._shown_main)
            self.canvas.create_text(cx + 2, cy + 2, text=name, fill="#000000",
                                    font=("Segoe UI", size, "bold"))
            self._items["logo_txt"] = self.canvas.create_text(
                cx, cy, text=name, fill=tint, font=("Segoe UI", size, "bold"))

    def _build_now_playing(self, w: int, h: int) -> None:
        # Items are recreated empty; reset the "last shown" trackers so the next
        # tick repopulates them (otherwise blank after a rebuild until the next
        # song).
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
        w, h = self._last_size
        if w <= 1 or h <= 1:
            return
        self._switch_seq += 1
        seq = self._switch_seq
        params = dict(
            w=w, h=h, accent=self._shown_accent, main=self._shown_main,
            logo=self._shown_logo, sig=self._shown_sig, portrait=self._shown_portrait,
            hero=self._shown_hero)
        threading.Thread(target=self._switch_worker, args=(seq, params),
                         name="stage-switch", daemon=True).start()

    def _switch_worker(self, seq: int, p: dict) -> None:
        try:
            assets = self._render_assets(
                p["w"], p["h"], accent=p["accent"], main=p["main"], logo=p["logo"],
                sig=p["sig"], portrait=p["portrait"], hero=p["hero"])
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
        if applied is None or self._closed or applied["size"] != self._last_size:
            return
        if self._anim is None:
            # No wipe running (e.g. cancelled by a resize): paint directly.
            self._bg_photo = ImageTk.PhotoImage(applied["bg"])
            self._sig_photo = (ImageTk.PhotoImage(applied["sig"])
                               if applied["sig"] is not None else None)
            self._paint_items(applied)
            return
        # Queue the swap as one-step-per-frame work, done while covered. The
        # portrait wrap is its own step too, so no frame ever does two wraps.
        self._queue_swap_steps(applied)

    def _queue_swap_steps(self, assets: dict) -> None:
        steps: List[Callable[[], None]] = []

        def wrap_portrait():
            self._cover_photo = (ImageTk.PhotoImage(assets["portrait"])
                                 if assets.get("portrait") is not None else None)

        def wrap_bg():
            self._bg_photo = ImageTk.PhotoImage(assets["bg"])

        def wrap_sig():
            self._sig_photo = (ImageTk.PhotoImage(assets["sig"])
                               if assets["sig"] is not None else None)

        def repaint():
            self._paint_items(assets)      # cheap creates (images pre-wrapped)
            self._create_cover(-self._slant())   # cover back on top, covering

        steps.extend([wrap_portrait, wrap_bg, wrap_sig, repaint])
        self._swap_steps = steps

    # ----------------------------------------------------------- cover wipe
    def _slant(self) -> int:
        return max(24, int(self._last_size[0] * SLANT_FRAC))

    def _cover_colors(self) -> Tuple[str, str]:
        panel = theming.rgb_to_hex(theming.scale(self._shown_main, 0.30))
        edge = theming.rgb_to_hex(self._shown_accent)
        return panel, edge

    def _create_cover(self, x: int) -> None:
        """A slanted panel (parallelogram, width w+slant) with an accent leading
        edge and the hero portrait. x = position of its bottom-left corner.
        Fully covering at x == -slant."""
        w, h = self._last_size
        s = self._slant()
        e = max(4, int(w * EDGE_FRAC))
        panel_fill, edge_fill = self._cover_colors()
        panel = self.canvas.create_polygon(
            x + s, 0, x + w + 2 * s, 0, x + w + s, h, x, h,
            fill=panel_fill, width=0)
        edge = self.canvas.create_polygon(
            x + s, 0, x + s + e, 0, x + e, h, x, h,
            fill=edge_fill, width=0)
        img = None
        if self._cover_photo is not None:
            img = self.canvas.create_image(
                x + s + w // 2, int(h * 0.5), image=self._cover_photo)
        self._cover = {"panel": panel, "edge": edge, "img": img, "x": x}

    def _move_cover(self, x: int, dy: int = 0) -> None:
        if not self._cover:
            return
        w, h = self._last_size
        s = self._slant()
        e = max(4, int(w * EDGE_FRAC))
        self.canvas.coords(self._cover["panel"],
                           x + s, 0, x + w + 2 * s, 0, x + w + s, h, x, h)
        self.canvas.coords(self._cover["edge"],
                           x + s, 0, x + s + e, 0, x + e, h, x, h)
        if self._cover["img"] is not None:
            self.canvas.coords(self._cover["img"],
                               x + s + w // 2, int(h * 0.5) + dy)
        self._cover["x"] = x

    def _destroy_cover(self) -> None:
        if self._cover:
            for key in ("panel", "edge", "img"):
                item = self._cover.get(key)
                if item is not None:
                    try:
                        self.canvas.delete(item)
                    except Exception:
                        pass
            self._cover = None
        self._cover_photo = None

    def _begin_wipe(self) -> None:
        """Start the cover sweep immediately on a hero change — before the new
        assets exist — so the switch feels instant."""
        self._cancel_anim()
        w, _h = self._last_size
        self._cover_photo = None
        self._create_cover(w)              # fully off-screen right
        self._anim = {"phase": "in", "t0": time.perf_counter()}

    def _cancel_anim(self) -> None:
        self._anim = None
        self._swap_steps = []
        self._destroy_cover()

    def _step_anim(self) -> None:
        a = self._anim
        if a is None:
            return
        w, _h = self._last_size
        s = self._slant()
        now = time.perf_counter()
        phase = a["phase"]

        if phase == "in":
            # Sweep from x=w (off right) to x=-s (fully covering).
            p = (now - a["t0"]) / T_IN
            if p >= 1.0:
                self._move_cover(-s)
                self._anim = {"phase": "swap", "t0": now}
                return
            self._move_cover(int(w - (w + s) * _smoothstep(p)))

        elif phase == "swap":
            # Fully covered. One swap step per frame (each is at most one big
            # PhotoImage wrap), so no single frame stalls.
            if self._swap_steps:
                step = self._swap_steps.pop(0)
                try:
                    step()
                except Exception:
                    self._swap_steps = []
                if not self._swap_steps:
                    self._anim = {"phase": "show", "t0": time.perf_counter()}
            # else: assets not ready yet — hold covered until they arrive.

        elif phase == "show":
            # Hold the portrait; give it a slow upward drift so it feels alive.
            p = (now - a["t0"]) / T_SHOW
            if p >= 1.0:
                self._anim = {"phase": "out", "t0": now}
                return
            drift = int(-self._last_size[1] * 0.015 * _smoothstep(p))
            self._move_cover(-s, dy=drift)

        else:  # "out": sweep from -s to fully off-left, revealing the new Stage.
            p = (now - a["t0"]) / T_OUT
            if p >= 1.0:
                self._destroy_cover()
                self._anim = None
                return
            self._move_cover(int(-s - (w + 2 * s) * _smoothstep(p)))

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
                self._begin_wipe()          # sweep starts this very frame
                self._start_switch()        # assets render off-thread
            # else: first paint happens via _on_canvas_configure → _rebuild

    def _wrap_pending_ladder(self) -> None:
        for _ in range(LADDER_PER_FRAME):
            if not self._ladder_pil:
                break
            self._logo_ladder.append(ImageTk.PhotoImage(self._ladder_pil.pop(0)))

    def _animate(self) -> None:
        if self._closed:
            return
        self._sync_state()
        self._poll_switch()

        if self._anim is not None:
            # Transition frames: the cover is the ONLY thing that moves. Pausing
            # the underlying animations keeps the canvas damage small so the
            # sweep itself renders fluidly even on large screens.
            self._step_anim()
        else:
            if self._ladder_pil:
                self._wrap_pending_ladder()
            self._update_logo_pulse()
            if self.cfg.show_now_playing:
                self._update_now_playing()
            self._update_visualizer()

        # Drift-corrected pacing: schedule relative to the ideal timeline, not
        # a fixed interval, so jitter doesn't accumulate.
        self._next_frame += self._frame_s
        now = time.perf_counter()
        if self._next_frame < now - 3 * self._frame_s:   # fell far behind: resync
            self._next_frame = now + self._frame_s
        delay_ms = max(1, int((self._next_frame - now) * 1000))
        self.top.after(delay_ms, self._animate)

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
