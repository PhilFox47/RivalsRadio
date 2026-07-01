"""The Stage — a hardware-accelerated (SDL/pygame) second-screen view.

This replaces the old Tk-canvas Stage. Tk's canvas is CPU-blitted and fights
high frame rates; pygame renders through SDL with real per-pixel alpha, so the
whole scene — bars, glow, particles, transitions — animates smoothly at the
configured FPS (up to 160).

Scene, back to front:
  gradient background (dual-tone: hero main → accent)         [#16]
  album-art ambience glow (tinted by the current album art)    [#7]
  floating accent particles                                    [#15]
  beat vignette (edges flare on kicks)                         [#5]
  beat glow ring behind the logo                               [#1]
  hero logo (tinted, pulses to the beat)
  hero signature (top-right)
  now playing (top-left; title slides/fades on track change)   [#10]
  visualizer: bottom bars OR radial around the logo, with
  falling peak caps                                            [#4, #2]
  live KDA strip (bottom-right)                                [#12]

Extra modes:
  hero switch  — slanted panel sweep with the hero's portrait and a big
                 typography moment for the hero name            [#11]
  match end    — VICTORY / DEFEAT takeover with the session record  [#13]
  idle         — when no music plays: clock, session stats, and a slow
                 roster portrait showcase                       [#14, #17]

The window runs on its own thread with its own SDL event loop; the app talks
to it only through StageState (already thread-safe).
F11 / double-click = fullscreen on the window's current monitor, Esc = exit.
"""

from __future__ import annotations

import math
import os
import queue
import random
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from . import theming
from .config import Config
from .stage_state import StageState
from .audio_visualizer import AudioVisualizer
from . import nowplaying

# Transition timings (seconds).
T_IN, T_SHOW, T_OUT = 0.32, 0.60, 0.38
SLANT_FRAC = 0.10
TAKEOVER_S = 3.0
IDLE_AFTER_S = 20.0        # no music for this long → idle showcase
IDLE_CYCLE_S = 9.0         # seconds per hero in the idle showcase

PULSE_STEPS = 16
PULSE_MAX = 1.16
N_PARTICLES = 30

BG_DARK = (7, 8, 12)


# ---------------------------------------------------------------- PIL helpers
def _open_rgba(path: Optional[str]) -> Optional[Image.Image]:
    if not path:
        return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def _fit(img: Image.Image, mw: int, mh: int) -> Image.Image:
    r = min(mw / img.width, mh / img.height)
    return img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                      Image.LANCZOS)


def _tint_white(img: Image.Image, rgb) -> Image.Image:
    solid = Image.new("RGB", img.size, tuple(rgb))
    out = ImageChops.multiply(img.convert("RGB"), solid).convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def _smoothstep(t: float) -> float:
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return t * t * (3.0 - 2.0 * t)


def _render_bg(w: int, h: int, main, accent) -> Image.Image:
    """Dual-tone diagonal gradient (main → accent over near-black) + glow."""
    cap = 1280
    if max(w, h) > cap:
        s = cap / max(w, h)
        rw, rh = max(1, int(w * s)), max(1, int(h * s))
    else:
        rw, rh = w, h
    yy, xx = np.mgrid[0:rh, 0:rw].astype(np.float32)
    t = (xx / max(1, rw - 1) + yy / max(1, rh - 1)) * 0.5   # 0 at TL → 1 at BR
    dark = np.array(BG_DARK, np.float32)
    c_main = dark + (np.array(main, np.float32) - dark) * 0.22
    c_acc = dark + (np.array(accent, np.float32) - dark) * 0.16
    arr = (c_main[None, None] * (1 - t)[..., None]
           + c_acc[None, None] * t[..., None]).astype(np.uint8)
    bg = Image.fromarray(arr, "RGB")
    # Soft accent glow toward the centre.
    glow = Image.new("RGB", (rw, rh), (0, 0, 0))
    from PIL import ImageDraw
    gd = ImageDraw.Draw(glow)
    cx, cy = rw // 2, int(rh * 0.5)
    rr = max(1, int(min(rw, rh) * 0.45))
    gd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
               fill=tuple(int(c * 0.45) for c in accent))
    glow = glow.filter(ImageFilter.GaussianBlur(max(1, rr // 2)))
    bg = ImageChops.screen(bg, glow)
    if (rw, rh) != (w, h):
        bg = bg.resize((w, h), Image.BILINEAR)
    return bg


def _radial_sprite(size: int, rgb, core: float = 1.0) -> Image.Image:
    """Soft radial dot/glow baked into RGB (for additive blits)."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    d = np.sqrt((x - c) ** 2 + (y - c) ** 2) / c
    fall = np.clip(1.0 - d, 0.0, 1.0) ** 2 * core
    arr = (fall[..., None] * np.array(rgb, np.float32)[None, None]).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _render_photo_bg(path: str, w: int, h: int, blur: int) -> Optional[Image.Image]:
    """Cover-fit a hero background picture to (w, h), blur and darken it so the
    foreground (logo, text, bars) stays readable."""
    img = _open_rgba(path)
    if img is None:
        return None
    img = img.convert("RGB")
    r = max(w / img.width, h / img.height)
    img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                     Image.LANCZOS)
    x = (img.width - w) // 2
    y = (img.height - h) // 2
    img = img.crop((x, y, x + w, y + h))
    if blur > 0:
        # Blur at a reduced size — visually identical, much cheaper at 4K.
        cap = 1280
        if max(w, h) > cap:
            s = cap / max(w, h)
            small = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.BILINEAR)
            small = small.filter(ImageFilter.GaussianBlur(max(1, int(blur * s))))
            img = small.resize((w, h), Image.BILINEAR)
        else:
            img = img.filter(ImageFilter.GaussianBlur(blur))
    return Image.eval(img, lambda v: int(v * 0.52))


def _vignette_sprite(w: int, h: int, rgb) -> Image.Image:
    """Accent-tinted edge vignette baked into RGB (additive)."""
    cap = 640
    s = cap / max(w, h) if max(w, h) > cap else 1.0
    rw, rh = max(1, int(w * s)), max(1, int(h * s))
    y, x = np.mgrid[0:rh, 0:rw].astype(np.float32)
    dx = np.abs(x / max(1, rw - 1) - 0.5) * 2
    dy = np.abs(y / max(1, rh - 1) - 0.5) * 2
    d = np.clip(np.maximum(dx, dy) * 1.15 - 0.55, 0, 1) ** 2
    arr = (d[..., None] * np.array(rgb, np.float32)[None, None] * 0.55).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    return img.resize((w, h), Image.BILINEAR) if (rw, rh) != (w, h) else img


def _avg_color(path: str):
    """Vibrant-ish average colour of an image file (for album ambience)."""
    try:
        im = Image.open(path).convert("RGB")
        im.thumbnail((24, 24))
        arr = np.asarray(im, np.float32).reshape(-1, 3)
        c = arr.mean(axis=0)
        import colorsys
        hh, ss, vv = colorsys.rgb_to_hsv(*(c / 255.0))
        ss = max(ss, 0.45)
        vv = max(vv, 0.55)
        r, g, b = colorsys.hsv_to_rgb(hh, ss, vv)
        return (int(r * 255), int(g * 255), int(b * 255))
    except Exception:
        return None


class StageWindow:
    """Public API (matches the old Tk Stage): alive, close(), set_fps(), focus()."""

    def __init__(self, parent, state: StageState, cfg: Config,
                 visualizer: AudioVisualizer) -> None:
        self.state = state
        self.cfg = cfg
        self.visualizer = visualizer
        self._fps = max(30, min(160, int(getattr(cfg, "stage_fps", 144))))
        self._closed = False
        self._focus_req = False
        self.visualizer.start()
        self._thread = threading.Thread(target=self._run, name="stage", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------- API
    @property
    def alive(self) -> bool:
        return not self._closed and self._thread.is_alive()

    def close(self) -> None:
        self._closed = True

    def set_fps(self, fps: int) -> None:
        self._fps = max(30, min(160, int(fps)))

    def focus(self) -> None:
        self._focus_req = True

    def refresh(self) -> None:
        """Re-render assets (e.g. after the background-blur setting changed)."""
        self._refresh_req = True

    # ------------------------------------------------------------- thread
    def _run(self) -> None:
        try:
            self._run_inner()
        except Exception:
            # The Stage runs on its own thread — dump the traceback so a crash
            # is diagnosable from ~/.rivalsradio/stage-error.log instead of the
            # window just silently closing.
            try:
                import traceback
                from .config import app_data_dir
                with open(os.path.join(app_data_dir(), "stage-error.log"), "w",
                          encoding="utf-8") as fh:
                    traceback.print_exc(file=fh)
            except Exception:
                pass
        finally:
            self._closed = True
            try:
                import pygame
                pygame.quit()
            except Exception:
                pass

    def _run_inner(self) -> None:
        if sys.platform.startswith("win"):
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
            except Exception:
                pass
        os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
        import pygame
        self.pg = pygame
        pygame.init()
        pygame.display.set_caption("RivalsRadio — Stage")
        self.screen = pygame.display.set_mode((960, 600), pygame.RESIZABLE)
        self._set_icon()
        self.clock = pygame.time.Clock()

        self.W, self.H = self.screen.get_size()
        self._windowed_size = (960, 600)
        self._fullscreen = False
        self._last_click = 0.0

        # Shown state.
        self._hero: Optional[str] = None
        self._accent = theming.hex_to_rgb(self.state.accent_hex)
        self._main = theming.hex_to_rgb(self.state.main_hex)
        self._logo_path = None
        self._sig_path = None
        self._portrait_path = None
        self._bgimg_path = None

        # Surfaces (rebuilt per hero/size).
        self._bg = None
        self._has_photo_bg = False
        self._vignette = None
        self._ladder: List = []
        self._sig = None
        self._portrait = None
        self._album_glow = None
        self._album_url = ""
        self._album_rgb = None
        self._art_surf = None

        # Async asset pipeline. Steps run one per frame, always — a batch is
        # "applied" once its steps finish (_applied_seq catches up to _seq).
        self._asset_q: "queue.Queue[Tuple[int, dict]]" = queue.Queue()
        self._seq = 0
        self._applied_seq = 0
        self._pending_seq = 0
        self._steps: List = []
        self._refresh_req = False

        # Animation state.
        self._anim: Optional[dict] = None       # wipe
        self._takeover: Optional[dict] = None   # victory/defeat
        self._match_seen = self.state.match_seq
        self._pulse = 0.0
        self._levels = [0.0] * self.visualizer.bands
        self._peaks = [0.0] * self.visualizer.bands
        self._last_play = time.perf_counter()
        self._idle: Optional[dict] = None
        self._title_anim = None
        self._last_title = None
        self._fonts: Dict = {}
        self._text_cache: Dict = {}
        self._particles = self._spawn_particles()
        self._dots = []

        self._request_assets()

        while not self._closed:
            self._handle_events()
            if self._closed:
                break
            if self._focus_req:
                self._focus_req = False
                self._raise_window()
            if self._refresh_req:
                self._refresh_req = False
                self._request_assets()
            self._poll_assets()
            self._sync_state()
            self._draw()
            pygame.display.flip()
            if self._fps >= 90:
                self.clock.tick_busy_loop(self._fps)
            else:
                self.clock.tick(self._fps)

        if sys.platform.startswith("win"):
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass

    def _set_icon(self) -> None:
        base = getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        png = os.path.join(base, "assets", "icon.png")
        if os.path.exists(png):
            try:
                self.pg.display.set_icon(self.pg.image.load(png))
            except Exception:
                pass

    # ------------------------------------------------------------- events
    def _handle_events(self) -> None:
        pg = self.pg
        for ev in pg.event.get():
            if ev.type == pg.QUIT:
                self._closed = True
            elif ev.type == pg.KEYDOWN:
                if ev.key == pg.K_F11:
                    self._toggle_fullscreen()
                elif ev.key == pg.K_ESCAPE and self._fullscreen:
                    self._toggle_fullscreen()
            elif ev.type == pg.MOUSEBUTTONDOWN and ev.button == 1:
                now = time.perf_counter()
                if now - self._last_click < 0.35:
                    self._toggle_fullscreen()
                    self._last_click = 0.0
                else:
                    self._last_click = now
            elif ev.type == pg.VIDEORESIZE and not self._fullscreen:
                self._on_resize(ev.w, ev.h)

    def _on_resize(self, w: int, h: int) -> None:
        if (w, h) == (self.W, self.H) or w < 100 or h < 100:
            return
        self.W, self.H = w, h
        self._windowed_size = (w, h) if not self._fullscreen else self._windowed_size
        self._fonts.clear()
        self._text_cache.clear()
        # Cheap immediate scale so nothing goes black, then re-render properly.
        if self._bg is not None:
            self._bg = self.pg.transform.smoothscale(self._bg, (w, h))
        self._vignette = None
        self._request_assets()

    def _monitor_rect(self) -> Optional[Tuple[int, int, int, int]]:
        if not sys.platform.startswith("win"):
            return None
        try:
            import ctypes
            from ctypes import wintypes
            info = self.pg.display.get_wm_info()
            hwnd = info.get("window")
            if not hwnd:
                return None
            hmon = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)

            class MI(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            mi = MI()
            mi.cbSize = ctypes.sizeof(MI)
            if not ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                return None
            r = mi.rcMonitor
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
        except Exception:
            return None

    def _toggle_fullscreen(self) -> None:
        pg = self.pg
        if not self._fullscreen:
            rect = self._monitor_rect()
            if rect is not None:
                x, y, w, h = rect
                os.environ["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"
                self.screen = pg.display.set_mode((w, h), pg.NOFRAME)
            else:
                self.screen = pg.display.set_mode((0, 0), pg.FULLSCREEN)
                w, h = self.screen.get_size()
            self._fullscreen = True
            self._on_resize(w, h)
        else:
            os.environ.pop("SDL_VIDEO_WINDOW_POS", None)
            os.environ["SDL_VIDEO_CENTERED"] = "1"
            w, h = self._windowed_size
            self.screen = pg.display.set_mode((w, h), pg.RESIZABLE)
            self._fullscreen = False
            self._on_resize(w, h)

    def _raise_window(self) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            hwnd = self.pg.display.get_wm_info().get("window")
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    # ------------------------------------------------------------- assets
    def _request_assets(self) -> None:
        """Render the current hero's images on a worker thread (pure PIL)."""
        self._seq += 1
        seq = self._seq
        p = dict(w=self.W, h=self.H, main=self._main, accent=self._accent,
                 logo=self._logo_path, sig=self._sig_path, por=self._portrait_path,
                 bgimg=self._bgimg_path,
                 blur=max(0, int(getattr(self.cfg, "stage_bg_blur", 12))))

        def work():
            try:
                bg = None
                if p["bgimg"]:
                    bg = _render_photo_bg(p["bgimg"], p["w"], p["h"], p["blur"])
                photo = bg is not None
                if bg is None:
                    bg = _render_bg(p["w"], p["h"], p["main"], p["accent"])
                ladder = []
                logo = _open_rgba(p["logo"])
                if logo is not None:
                    base = _tint_white(_fit(logo, int(p["w"] * 0.44), int(p["h"] * 0.44)),
                                       p["main"])
                    for i in range(PULSE_STEPS):
                        sc = 1.0 + (PULSE_MAX - 1.0) * i / (PULSE_STEPS - 1)
                        fr = base if i == 0 else base.resize(
                            (max(1, int(base.width * sc)), max(1, int(base.height * sc))),
                            Image.BILINEAR)
                        ladder.append((fr.tobytes(), fr.size))
                sig = _open_rgba(p["sig"])
                sig_t = None
                if sig is not None:
                    f = _fit(sig, int(p["w"] * 0.30), int(p["h"] * 0.17))
                    sig_t = (f.tobytes(), f.size)
                por = _open_rgba(p["por"])
                por_t = None
                if por is not None:
                    f = _fit(por, int(p["w"] * 0.52), int(p["h"] * 0.84))
                    por_t = (f.tobytes(), f.size)
                vig = _vignette_sprite(p["w"], p["h"], p["accent"])
                self._asset_q.put((seq, dict(
                    size=(p["w"], p["h"]),
                    bg=(bg.tobytes(), bg.size), photo=photo,
                    ladder=ladder, sig=sig_t, por=por_t,
                    vig=(vig.tobytes(), vig.size))))
            except Exception:
                pass

        threading.Thread(target=work, name="stage-assets", daemon=True).start()

    def _surf_rgba(self, blob) -> Optional["object"]:
        if blob is None:
            return None
        data, size = blob
        return self.pg.image.frombuffer(data, size, "RGBA").convert_alpha()

    def _surf_rgb(self, blob):
        data, size = blob
        return self.pg.image.frombuffer(data, size, "RGB").convert()

    def _poll_assets(self) -> None:
        got = None
        got_seq = 0
        try:
            while True:
                seq, a = self._asset_q.get_nowait()
                if seq == self._seq:
                    got, got_seq = a, seq
        except queue.Empty:
            pass
        if got is None or got["size"] != (self.W, self.H):
            return
        # Spread the surface conversions over frames (one per frame) so applying
        # a batch never produces a long frame. These steps replace any stale
        # queued steps from a superseded batch.
        a = got

        def take_portrait():
            self._portrait = self._surf_rgba(a["por"])

        def take_ladder():
            self._ladder = [self._surf_rgba(fr) for fr in a["ladder"][:1]]
            self._ladder_pending = a["ladder"][1:]

        def take_bg():
            self._bg = self._surf_rgb(a["bg"])
            self._has_photo_bg = bool(a.get("photo"))

        def take_sig():
            self._sig = self._surf_rgba(a["sig"])

        def take_vig():
            self._vignette = self._surf_rgb(a["vig"])

        # Portrait first: the wipe wants it as early as possible.
        self._steps = [take_portrait, take_bg, take_ladder, take_sig, take_vig]
        self._pending_seq = got_seq
        self._dots = []   # re-tint particles to the (possibly new) accent

    def _run_steps(self) -> None:
        """Run one pending asset step per frame (always, wipe or not)."""
        if not self._steps:
            return
        step = self._steps.pop(0)
        try:
            step()
        except Exception:
            self._steps = []
        if not self._steps:
            self._applied_seq = self._pending_seq

    def _wrap_ladder_lazily(self) -> None:
        pend = getattr(self, "_ladder_pending", None)
        if pend:
            for _ in range(2):
                if not pend:
                    break
                self._ladder.append(self._surf_rgba(pend.pop(0)))
            self._ladder_pending = pend

    # ------------------------------------------------------------- sync
    def _sync_state(self) -> None:
        st = self.state
        hero = st.hero
        accent = theming.hex_to_rgb(st.accent_hex)
        main = theming.hex_to_rgb(st.main_hex)
        if (hero != self._hero or accent != self._accent or main != self._main
                or st.logo_path != self._logo_path or st.signature_path != self._sig_path
                or st.portrait_path != self._portrait_path
                or st.background_path != self._bgimg_path):
            first = self._hero is None and self._bg is None
            self._hero = hero
            self._accent = accent
            self._main = main
            self._logo_path = st.logo_path
            self._sig_path = st.signature_path
            self._portrait_path = st.portrait_path
            self._bgimg_path = st.background_path
            self._request_assets()
            if not first:
                self._anim = {"phase": "in", "t0": time.perf_counter()}
                self._idle = None

        # Victory / defeat takeover trigger.
        if st.match_seq != self._match_seen:
            self._match_seen = st.match_seq
            self._takeover = {"t0": time.perf_counter(), "result": st.match_result}

        # Idle detection.
        if st.track.is_playing:
            self._last_play = time.perf_counter()
            self._idle = None
        elif (self._idle is None and self._anim is None
              and time.perf_counter() - self._last_play > IDLE_AFTER_S):
            self._idle = {"t0": time.perf_counter(), "i": 0, "surf": None, "prev": None}

    # ------------------------------------------------------------- fonts/text
    def _font(self, key: str, px: int, bold: bool = True):
        k = (key, px, bold)
        f = self._fonts.get(k)
        if f is None:
            f = self.pg.font.SysFont("Segoe UI", px, bold=bold)
            self._fonts[k] = f
        return f

    def _text(self, s: str, px: int, color, bold: bool = True):
        k = (s, px, color, bold)
        surf = self._text_cache.get(k)
        if surf is None:
            surf = self._font("t", px, bold).render(s, True, color)
            if len(self._text_cache) > 220:
                self._text_cache.clear()
            self._text_cache[k] = surf
        return surf

    # ------------------------------------------------------------- particles
    def _spawn_particles(self) -> List[dict]:
        rng = random.Random(7)
        return [dict(x=rng.random(), y=rng.random(), s=rng.uniform(0.4, 1.0),
                     v=rng.uniform(0.008, 0.03), ph=rng.uniform(0, math.tau))
                for _ in range(N_PARTICLES)]

    def _ensure_dots(self) -> None:
        if self._dots:
            return
        for px in (26, 40, 58):
            im = _radial_sprite(px, self._accent, core=0.55)
            self._dots.append(self._surf_rgb((im.tobytes(), im.size)))

    def _draw_particles(self, dt: float) -> None:
        self._ensure_dots()
        add = self.pg.BLEND_ADD
        H, W = self.H, self.W
        for p in self._particles:
            p["y"] -= p["v"] * dt
            p["ph"] += dt * 0.7
            if p["y"] < -0.05:
                p["y"] = 1.05
                p["x"] = random.random()
            dot = self._dots[int(p["s"] * 2.999)]
            x = (p["x"] + math.sin(p["ph"]) * 0.012) * W - dot.get_width() / 2
            self.screen.blit(dot, (x, p["y"] * H - dot.get_height() / 2),
                             special_flags=add)

    # ------------------------------------------------------------- drawing
    def _beat(self) -> float:
        try:
            return float(self.visualizer.get_beat())
        except Exception:
            return 0.0

    def _draw(self) -> None:
        dt = min(0.05, self.clock.get_time() / 1000.0) or (1.0 / self._fps)
        in_wipe = self._anim is not None

        # Apply pending assets one step per frame — ALWAYS, so the very first
        # load (no wipe) and late-arriving batches land too, never going stale.
        if self._steps:
            self._run_steps()
        else:
            self._wrap_ladder_lazily()

        if self._idle is not None:
            self._draw_idle(dt)
            return

        # --- base scene -----------------------------------------------
        if self._bg is not None:
            self.screen.blit(self._bg, (0, 0))
        else:
            self.screen.fill(BG_DARK)

        self._update_album_ambience()
        # Album ambience only over the gradient background — on a photo
        # background it would wash the picture out.
        if self._album_glow is not None and not self._has_photo_bg:
            self.screen.blit(self._album_glow,
                             ((self.W - self._album_glow.get_width()) // 2,
                              (self.H - self._album_glow.get_height()) // 2),
                             special_flags=self.pg.BLEND_ADD)

        self._draw_particles(dt)

        beat = self._beat()
        attack = beat > self._pulse
        self._pulse += (beat - self._pulse) * (0.55 if attack else 0.16)

        if self._vignette is not None and self._pulse > 0.04:
            self._vignette.set_alpha(int(150 * self._pulse))
            self.screen.blit(self._vignette, (0, 0), special_flags=self.pg.BLEND_ADD)

        cx, cy = self.W // 2, int(self.H * 0.46)
        # Logo (pulse ladder) or hero-name fallback.
        if self._ladder:
            idx = int(self._pulse * (PULSE_STEPS - 1))
            idx = max(0, min(idx, len(self._ladder) - 1))
            fr = self._ladder[idx]
            self.screen.blit(fr, (cx - fr.get_width() // 2, cy - fr.get_height() // 2))
        else:
            name = self._hero or "Waiting for hero…"
            t = self._text(name, max(24, int(self.H * 0.10)), self._main)
            self.screen.blit(t, (cx - t.get_width() // 2, cy - t.get_height() // 2))

        if self._sig is not None:
            pad = int(self.H * 0.04)
            self.screen.blit(self._sig, (self.W - pad - self._sig.get_width(), pad))

        if self.cfg.show_now_playing:
            self._draw_now_playing(dt)
        self._draw_visualizer(dt)
        self._draw_kda()

        hint = self._text("Double-click / F11 fullscreen · Esc exit",
                          max(10, int(self.H * 0.014)), (86, 92, 99), bold=False)
        self.screen.blit(hint, (self.W - hint.get_width() - 12,
                                self.H - hint.get_height() - 6))

        if in_wipe:
            self._draw_wipe()
        if self._takeover is not None:
            self._draw_takeover()

    # ----- album ambience -------------------------------------------- [#7]
    def _update_album_ambience(self) -> None:
        url = self.state.track.album_art_url
        if url == self._album_url:
            return
        self._album_url = url
        self._album_rgb = None
        self._album_glow = None
        self._art_surf = None
        if not url:
            return
        path = nowplaying.art_path_for(url)
        if not path:
            return
        rgb = _avg_color(path)
        if rgb is None:
            return
        self._album_rgb = rgb
        d = int(min(self.W, self.H) * 0.9)
        im = _radial_sprite(d, rgb, core=0.30)
        self._album_glow = self._surf_rgb((im.tobytes(), im.size))
        try:
            art = Image.open(path).convert("RGB")
            a = int(self.H * 0.13)
            art = art.resize((a, a), Image.LANCZOS)
            self._art_surf = self._surf_rgb((art.tobytes(), art.size))
        except Exception:
            pass

    # ----- now playing ------------------------------------------------ [#10]
    def _draw_now_playing(self, dt: float) -> None:
        tr = self.state.track
        pad = int(self.H * 0.045)
        art = int(self.H * 0.13)
        x, y = pad, pad
        self.pg.draw.rect(self.screen, (17, 20, 24), (x, y, art, art), border_radius=6)
        if self._art_surf is not None:
            self.screen.blit(self._art_surf, (x, y))

        title = tr.title or "—"
        if title != self._last_title:
            self._last_title = title
            self._title_anim = {"t0": time.perf_counter()}
        tx = x + art + int(self.W * 0.014)
        ts = max(13, int(self.H * 0.031))
        t_surf = self._text(title, ts, (255, 255, 255))
        a_surf = self._text(tr.artist or "", max(11, int(self.H * 0.022)),
                            (201, 201, 201), bold=False)
        off, alpha = 0, 255
        if self._title_anim is not None:
            p = (time.perf_counter() - self._title_anim["t0"]) / 0.45
            if p >= 1.0:
                self._title_anim = None
            else:
                e = _smoothstep(p)
                off = int((1.0 - e) * self.H * 0.03)
                alpha = int(60 + 195 * e)
        t_surf.set_alpha(alpha)
        a_surf.set_alpha(alpha)
        self.screen.blit(t_surf, (tx, y + off))
        self.screen.blit(a_surf, (tx, y + int(ts * 1.5) + off))
        t_surf.set_alpha(255)
        a_surf.set_alpha(255)

        pb_w = int(self.W * 0.30)
        pb_h = max(3, int(self.H * 0.008))
        pb_y = y + art - pb_h - 2
        frac = (tr.live_progress_ms() / tr.duration_ms) if tr.duration_ms else 0.0
        frac = max(0.0, min(1.0, frac))
        self.pg.draw.rect(self.screen, (42, 46, 51), (tx, pb_y, pb_w, pb_h),
                          border_radius=pb_h // 2)
        if frac > 0:
            self.pg.draw.rect(self.screen, (255, 255, 255),
                              (tx, pb_y, int(pb_w * frac), pb_h),
                              border_radius=pb_h // 2)

    # ----- visualizer ------------------------------------------- [#4, #2]
    def _draw_visualizer(self, dt: float) -> None:
        spectrum = self.visualizer.get_spectrum()
        n = len(self._levels)
        alpha = max(0.12, min(0.6, 0.5 * (60.0 / self._fps)))
        peak_fall = 0.55 * dt          # peak caps fall speed (fraction/s)
        for i in range(n):
            target = float(spectrum[i]) if i < len(spectrum) else 0.0
            target = max(target, 0.03)
            self._levels[i] += (target - self._levels[i]) * alpha
            self._peaks[i] = max(self._peaks[i] - peak_fall, self._levels[i])

        if getattr(self.cfg, "stage_style", "bars") == "radial":
            self._draw_radial_bars()
        else:
            self._draw_bottom_bars()

    def _draw_bottom_bars(self) -> None:
        W, H = self.W, self.H
        n = len(self._levels)
        margin = int(W * 0.04)
        usable = W - 2 * margin
        gap = max(1, int(usable / n * 0.25))
        bw = max(1, (usable - gap * (n - 1)) // n)
        base = int(H * 0.97)
        max_h = max(8, int(H * 0.30))
        col = self._accent
        cap_col = tuple(min(255, int(c * 1.35)) for c in col)
        rect = self.pg.draw.rect
        for i in range(n):
            x = margin + i * (bw + gap)
            bh = int(2 + self._levels[i] * max_h)
            rect(self.screen, col, (x, base - bh, bw, bh))
            py = base - int(2 + self._peaks[i] * max_h)
            rect(self.screen, cap_col, (x, py - 3, bw, 3))

    def _draw_radial_bars(self) -> None:
        """Bars radiate outward FROM the logo's edge."""
        cx, cy = self.W // 2, int(self.H * 0.46)
        if self._ladder:
            fr = self._ladder[0]
            r0 = int(max(fr.get_width(), fr.get_height()) * 0.5 * 1.12)
        else:
            r0 = int(min(self.W, self.H) * 0.18)
        n = len(self._levels)
        lmax = int(min(self.W, self.H) * 0.20)
        col = self._accent
        cap_col = tuple(min(255, int(c * 1.35)) for c in col)
        width = max(2, int(2 * math.pi * r0 / n * 0.45))
        line = self.pg.draw.line
        for i in range(n):
            ang = -math.pi / 2 + (i / n) * math.tau
            ca, sa = math.cos(ang), math.sin(ang)
            ln = 2 + self._levels[i] * lmax
            x0, y0 = cx + ca * r0, cy + sa * r0
            line(self.screen, col, (x0, y0), (cx + ca * (r0 + ln), cy + sa * (r0 + ln)),
                 width)
            pr = r0 + 2 + self._peaks[i] * lmax
            line(self.screen, cap_col, (cx + ca * pr, cy + sa * pr),
                 (cx + ca * (pr + 3), cy + sa * (pr + 3)), width)

    # ----- KDA strip -------------------------------------------------- [#12]
    def _draw_kda(self) -> None:
        k, d, a = self.state.kda
        if (k, d, a) == (0, 0, 0):
            return
        px = max(12, int(self.H * 0.020))
        s = self._text(f"K {k}   D {d}   A {a}", px, (220, 224, 228))
        self.screen.blit(s, (self.W - s.get_width() - 14,
                             self.H - s.get_height() - int(self.H * 0.035)))

    # ----- hero switch wipe ------------------------------------- [#11]
    def _draw_wipe(self) -> None:
        a = self._anim
        if a is None:
            return
        W, H = self.W, self.H
        s = max(24, int(W * SLANT_FRAC))
        now = time.perf_counter()
        phase = a["phase"]

        if phase == "in":
            p = (now - a["t0"]) / T_IN
            if p >= 1.0:
                self._anim = {"phase": "hold", "t0": now}
                x = -s
            else:
                x = int(W - (W + s) * _smoothstep(p))
        elif phase == "hold":
            # Fully covered; wait until the NEW hero's asset batch has been
            # fully applied (sequence caught up), then show the portrait.
            x = -s
            if self._applied_seq == self._seq:
                self._anim = {"phase": "show", "t0": now}
        elif phase == "show":
            p = (now - a["t0"]) / T_SHOW
            if p >= 1.0:
                self._anim = {"phase": "out", "t0": now}
                p = 0.0
            x = -s
        else:  # out
            p = (now - a["t0"]) / T_OUT
            if p >= 1.0:
                self._anim = None
                return
            x = int(-s - (W + 2 * s) * _smoothstep(p))

        panel = tuple(int(c * 0.30) for c in self._main)
        pts = [(x + s, 0), (x + W + 2 * s, 0), (x + W + s, H), (x, H)]
        self.pg.draw.polygon(self.screen, panel, pts)
        e = max(4, int(W * 0.008))
        self.pg.draw.polygon(self.screen, self._accent,
                             [(x + s, 0), (x + s + e, 0), (x + e, H), (x, H)])

        # Portrait + hero name typography ride the panel.
        drift = 0
        if phase == "show":
            drift = int(-H * 0.015 * _smoothstep((now - a["t0"]) / T_SHOW))
        pcx = x + s + W // 2
        if self._portrait is not None:
            self.screen.blit(self._portrait,
                             (pcx - self._portrait.get_width() // 2,
                              H // 2 - self._portrait.get_height() // 2 + drift))
        if self._hero:
            name = self._text(self._hero.upper(), max(28, int(H * 0.085)),
                              (255, 255, 255))
            sh = self._text(self._hero.upper(), max(28, int(H * 0.085)), (0, 0, 0))
            ny = int(H * 0.78)
            self.screen.blit(sh, (pcx - name.get_width() // 2 + 3, ny + 3))
            self.screen.blit(name, (pcx - name.get_width() // 2, ny))

    # ----- victory / defeat takeover ------------------------------ [#13]
    def _draw_takeover(self) -> None:
        t = self._takeover
        p = (time.perf_counter() - t["t0"]) / TAKEOVER_S
        if p >= 1.0:
            self._takeover = None
            return
        fade = min(1.0, p / 0.12) * min(1.0, (1.0 - p) / 0.2)
        veil = self.pg.Surface((self.W, self.H))
        veil.fill((5, 6, 10))
        veil.set_alpha(int(200 * fade))
        self.screen.blit(veil, (0, 0))
        win = "vic" in t["result"].lower() or "win" in t["result"].lower()
        word = "VICTORY" if win else "DEFEAT"
        color = self._accent if win else (229, 72, 77)
        big = self._text(word, max(40, int(self.H * 0.16)), color)
        big.set_alpha(int(255 * fade))
        self.screen.blit(big, ((self.W - big.get_width()) // 2,
                               int(self.H * 0.36) - big.get_height() // 2))
        big.set_alpha(255)
        ses = self.state.session or {}
        line = f"Session  {ses.get('wins', 0)}W – {ses.get('losses', 0)}L"
        sub = self._text(line, max(14, int(self.H * 0.03)), (222, 226, 230))
        sub.set_alpha(int(255 * fade))
        self.screen.blit(sub, ((self.W - sub.get_width()) // 2, int(self.H * 0.52)))
        sub.set_alpha(255)

    # ----- idle showcase --------------------------------------- [#14, #17]
    def _draw_idle(self, dt: float) -> None:
        idle = self._idle
        self.screen.fill(BG_DARK)
        roster = [r for r in self.state.roster if r.get("portrait")]
        now = time.perf_counter()

        if roster:
            i = int((now - idle["t0"]) / IDLE_CYCLE_S) % len(roster)
            if idle.get("shown") != i:
                idle["shown"] = i
                entry = roster[i]
                por = _open_rgba(entry["portrait"])
                if por is not None:
                    f = _fit(por, int(self.W * 0.44), int(self.H * 0.72))
                    idle["prev"] = idle.get("surf")
                    idle["surf"] = self._surf_rgba((f.tobytes(), f.size))
                    idle["name"] = entry.get("hero", "")
                    idle["accent"] = theming.hex_to_rgb(
                        entry.get("accent") or "#1DB954")
                    idle["t_show"] = now
            surf = idle.get("surf")
            if surf is not None:
                p = min(1.0, (now - idle.get("t_show", now)) / 0.8)
                prev = idle.get("prev")
                if prev is not None and p < 1.0:
                    prev.set_alpha(int(140 * (1 - p)))
                    self.screen.blit(prev, ((self.W - prev.get_width()) // 2,
                                            int(self.H * 0.52) - prev.get_height() // 2))
                surf.set_alpha(int(60 + 130 * p))
                self.screen.blit(surf, ((self.W - surf.get_width()) // 2,
                                        int(self.H * 0.52) - surf.get_height() // 2))
                surf.set_alpha(255)
                name = self._text(idle.get("name", ""), max(18, int(self.H * 0.045)),
                                  idle.get("accent", self._accent))
                name.set_alpha(150)
                self.screen.blit(name, ((self.W - name.get_width()) // 2,
                                        int(self.H * 0.86)))
                name.set_alpha(255)

        clock_s = self._text(time.strftime("%H:%M"), max(48, int(self.H * 0.16)),
                             (236, 237, 237))
        self.screen.blit(clock_s, ((self.W - clock_s.get_width()) // 2,
                                   int(self.H * 0.12)))
        ses = self.state.session or {}
        bits = []
        if ses.get("playtime"):
            bits.append(f"session {ses['playtime']}")
        if ses.get("matches"):
            bits.append(f"{ses.get('wins', 0)}W – {ses.get('losses', 0)}L")
        if bits:
            sub = self._text("   ·   ".join(bits), max(13, int(self.H * 0.026)),
                             (143, 149, 156), bold=False)
            self.screen.blit(sub, ((self.W - sub.get_width()) // 2,
                                   int(self.H * 0.12) + clock_s.get_height() + 8))
        self._draw_particles(dt)
