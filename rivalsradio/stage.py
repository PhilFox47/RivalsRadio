"""The Stage — a GPU-rendered (SDL/pygame) audio-visualizer for a second screen.

Scene, back to front:
  background   hero background picture (cover-fit, blurred, dimmed) — or a
               living gradient: two soft colour glows drifting slowly over a
               near-black base
  particles    soft accent motes floating upward (additive)
  glow         breathing radial glow behind the logo (main colour + beat)
  logo         hero logo (white art tinted the Main colour), pulsing to the beat
  signature    top-right
  now playing  top-left card: rounded album art, title/artist, progress
  visualizer   gradient bars with rounded caps and falling peak markers

Hero switches play a portrait-led panel sweep: the new portrait is rendered
*before* the animation starts, rides the panel with the hero's name, and the
scene is rebuilt behind the panel while the screen is covered.

Engineering notes (each prevents a measured stutter class):
- 1 ms Windows timer resolution while open (Tk/SDL timers are ~15.6 ms
  otherwise), busy-wait pacing at high FPS.
- All PIL work happens on a worker thread; the render thread only wraps
  finished images into surfaces, at most one big wrap per frame.
- The animation loop is allocation-free: pulse = ladder index, bars = subrect
  blits of a pre-rendered gradient strip.
"""

from __future__ import annotations

import math
import os
import queue
import random
import sys
import threading
import time
import traceback
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import colors, paths, spotify
from .config import Config
from .feed import Feed

PULSE_STEPS = 16
PULSE_MAX = 1.15
T_IN, T_SHOW, T_OUT = 0.30, 0.55, 0.36     # switch animation timings (s)
SLANT = 0.10                               # panel edge slant (fraction of W)
N_PARTICLES = 26
BG_BASE = (8, 9, 13)


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
    """White-on-transparent art → the given colour, alpha preserved."""
    solid = Image.new("RGB", img.size, tuple(rgb))
    out = ImageChops.multiply(img.convert("RGB"), solid).convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _soft_dot(size: int, rgb, core: float = 1.0) -> Image.Image:
    """Radial falloff baked into RGB — for additive blits."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    d = np.sqrt((x - c) ** 2 + (y - c) ** 2) / c
    fall = np.clip(1.0 - d, 0.0, 1.0) ** 2 * core
    return Image.fromarray(
        (fall[..., None] * np.array(rgb, np.float32)).astype(np.uint8), "RGB")


def _photo_bg(path: str, w: int, h: int, blur: int, dim: int) -> Optional[Image.Image]:
    img = _open_rgba(path)
    if img is None:
        return None
    img = img.convert("RGB")
    r = max(w / img.width, h / img.height)
    img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                     Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    img = img.crop((x, y, x + w, y + h))
    if blur > 0:
        cap = 1280                          # blur small, upscale — same look, fast
        if max(w, h) > cap:
            s = cap / max(w, h)
            small = img.resize((max(1, int(w * s)), max(1, int(h * s))),
                               Image.BILINEAR)
            small = small.filter(ImageFilter.GaussianBlur(max(1, int(blur * s))))
            img = small.resize((w, h), Image.BILINEAR)
        else:
            img = img.filter(ImageFilter.GaussianBlur(blur))
    factor = max(0.2, 1.0 - 0.8 * max(0, min(100, dim)) / 100.0)
    return Image.eval(img, lambda v: int(v * factor))


def _gradient_bg(w: int, h: int, main, accent) -> Image.Image:
    """Fallback background: deep diagonal blend of the hero colours."""
    cap = 1280
    s = cap / max(w, h) if max(w, h) > cap else 1.0
    rw, rh = max(1, int(w * s)), max(1, int(h * s))
    yy, xx = np.mgrid[0:rh, 0:rw].astype(np.float32)
    t = (xx / max(1, rw - 1) + yy / max(1, rh - 1)) * 0.5
    base = np.array(BG_BASE, np.float32)
    c0 = base + (np.array(main, np.float32) - base) * 0.20
    c1 = base + (np.array(accent, np.float32) - base) * 0.14
    arr = (c0[None, None] * (1 - t)[..., None]
           + c1[None, None] * t[..., None]).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    return img.resize((w, h), Image.BILINEAR) if (rw, rh) != (w, h) else img


def _rounded(img: Image.Image, radius: int) -> Image.Image:
    """Round the corners of an image (RGBA out)."""
    mask = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, img.width - 1, img.height - 1],
                        radius=radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def _bar_strip(width: int, height: int, accent) -> Image.Image:
    """Vertical gradient strip for the bars: accent at the base rising to a
    brighter tip. Bars blit a bottom-anchored subrect of this, so every bar
    height keeps the same anchored gradient with zero per-frame scaling."""
    top = colors.scale(accent, 1.45)
    t = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None, None]
    arr = (np.array(top, np.float32) * t
           + np.array(accent, np.float32) * (1 - t)).astype(np.uint8)
    return Image.fromarray(np.repeat(arr, width, axis=1), "RGB")


class StageWindow:
    """Runs on its own thread. API: alive, close(), focus(), set_fps()."""

    def __init__(self, cfg: Config, feed: Feed) -> None:
        self.cfg = cfg
        self.feed = feed
        self._fps = max(30, min(160, int(cfg.stage.fps)))
        self._closed = False
        self._focus_req = False
        self._thread = threading.Thread(target=self._run, name="stage", daemon=True)
        self._thread.start()

    # ----- API ----------------------------------------------------------
    @property
    def alive(self) -> bool:
        return not self._closed and self._thread.is_alive()

    def close(self) -> None:
        self._closed = True

    def focus(self) -> None:
        self._focus_req = True

    def set_fps(self, fps: int) -> None:
        self._fps = max(30, min(160, int(fps)))

    def refresh(self) -> None:
        """Re-render scene assets (after blur/dim/art changes)."""
        self._refresh_req = True

    # ----- thread body ----------------------------------------------------
    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            try:
                with open(os.path.join(paths.data_dir(), "stage-error.log"),
                          "w", encoding="utf-8") as fh:
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

    def _loop(self) -> None:
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
        self.screen = pygame.display.set_mode((980, 620), pygame.RESIZABLE)
        icon = paths.bundled("assets", "icon.png")
        if os.path.exists(icon):
            try:
                pygame.display.set_icon(pygame.image.load(icon))
            except Exception:
                pass
        self.clock = pygame.time.Clock()
        self.W, self.H = self.screen.get_size()
        self._windowed = (980, 620)
        self._fullscreen = False
        self._last_click = 0.0
        self._refresh_req = False

        # Scene state.
        self._seen_seq = -1
        self._visuals = None
        self._first_paint = True

        # Surfaces (owned by this thread).
        self._bg = None
        self._has_photo = False
        self._glow = None
        self._ladder: List = []
        self._ladder_pending: List = []
        self._sig = None
        self._portrait = None
        self._strip = None                 # bar gradient strip
        self._strip_h = 0
        self._panel = None
        self._dots: List = []
        self._np_card = None

        # Asset pipeline.
        self._q: "queue.Queue[Tuple[int, dict]]" = queue.Queue()
        self._req = 0
        self._applied = 0
        self._steps: List = []
        self._pending_seq = 0

        # Animation + live values.
        self._anim: Optional[dict] = None
        self._pulse = 0.0
        self._glow_t = random.random() * 10
        self._levels = [0.0] * 56
        self._peaks = [0.0] * 56
        self._particles = [dict(x=random.random(), y=random.random(),
                                s=random.uniform(0.35, 1.0),
                                v=random.uniform(0.008, 0.03),
                                ph=random.uniform(0, math.tau))
                           for _ in range(N_PARTICLES)]
        self._fonts: Dict = {}
        self._texts: Dict = {}
        self._art_surf = None
        self._art_url = ""
        self._art_retry = 0.0
        self._last_title = None
        self._title_t = 0.0

        while not self._closed:
            self._events()
            if self._closed:
                break
            if self._focus_req:
                self._focus_req = False
                self._raise()
            if self._refresh_req:
                self._refresh_req = False
                self._request_render()
            self._poll()
            self._sync()
            self._draw()
            pygame.display.flip()
            # Busy-wait pacing is only worth its precision in steady state —
            # while a render worker is busy it would starve that thread of the
            # GIL (the sleeping tick yields properly).
            waiting = (self._anim is not None or self._steps
                       or self._ladder_pending)
            if self._fps >= 90 and not waiting:
                self.clock.tick_busy_loop(self._fps)
            else:
                self.clock.tick(self._fps)

        if sys.platform.startswith("win"):
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass

    # ----- window ---------------------------------------------------------
    def _events(self) -> None:
        pg = self.pg
        for ev in pg.event.get():
            if ev.type == pg.QUIT:
                self._closed = True
            elif ev.type == pg.KEYDOWN:
                if ev.key == pg.K_F11 or (ev.key == pg.K_ESCAPE and self._fullscreen):
                    self._toggle_fullscreen()
            elif ev.type == pg.MOUSEBUTTONDOWN and ev.button == 1:
                now = time.perf_counter()
                if now - self._last_click < 0.35:
                    self._toggle_fullscreen()
                    self._last_click = 0.0
                else:
                    self._last_click = now
            elif ev.type == pg.VIDEORESIZE and not self._fullscreen:
                self._resized(ev.w, ev.h)

    def _resized(self, w: int, h: int) -> None:
        if (w, h) == (self.W, self.H) or w < 120 or h < 120:
            return
        self.W, self.H = w, h
        if not self._fullscreen:
            self._windowed = (w, h)
        self._fonts.clear()
        self._texts.clear()
        self._np_card = None
        self._art_url = ""              # art is size-dependent: reload
        self._art_retry = 0.0
        if self._bg is not None:        # instant stretch, then a proper render
            self._bg = self.pg.transform.smoothscale(self._bg, (w, h))
        self._request_render()

    def _monitor_rect(self) -> Optional[Tuple[int, int, int, int]]:
        if not sys.platform.startswith("win"):
            return None
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = self.pg.display.get_wm_info().get("window")
            if not hwnd:
                return None
            hmon = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)

            class MI(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD),
                            ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD)]
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
            if rect:
                x, y, w, h = rect
                os.environ["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"
                self.screen = pg.display.set_mode((w, h), pg.NOFRAME)
            else:
                self.screen = pg.display.set_mode((0, 0), pg.FULLSCREEN)
                w, h = self.screen.get_size()
            self._fullscreen = True
            self._resized(w, h)
        else:
            os.environ.pop("SDL_VIDEO_WINDOW_POS", None)
            os.environ["SDL_VIDEO_CENTERED"] = "1"
            w, h = self._windowed
            self.screen = pg.display.set_mode((w, h), pg.RESIZABLE)
            self._fullscreen = False
            self._resized(w, h)

    def _raise(self) -> None:
        if sys.platform.startswith("win"):
            try:
                import ctypes
                hwnd = self.pg.display.get_wm_info().get("window")
                if hwnd:
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    # ----- asset pipeline ---------------------------------------------------
    def _request_render(self) -> None:
        """Render the current visuals' images off-thread (pure PIL)."""
        v = self._visuals
        if v is None:
            return
        self._req += 1
        req = self._req
        w, h = self.W, self.H
        main = colors.hex_to_rgb(v.main_hex)
        accent = colors.hex_to_rgb(v.accent_hex)
        blur = int(self.cfg.stage.bg_blur)
        dim = int(self.cfg.stage.bg_dim)
        logo_p, sig_p, por_p, bg_p = v.logo, v.signature, v.portrait, v.background

        def work() -> None:
            try:
                bg = _photo_bg(bg_p, w, h, blur, dim) if bg_p else None
                photo = bg is not None
                if bg is None:
                    bg = _gradient_bg(w, h, main, accent)

                ladder = []
                logo = _open_rgba(logo_p)
                if logo is not None:
                    base = _tint_white(_fit(logo, int(w * 0.42), int(h * 0.42)), main)
                    for i in range(PULSE_STEPS):
                        sc = 1.0 + (PULSE_MAX - 1.0) * i / (PULSE_STEPS - 1)
                        fr = base if i == 0 else base.resize(
                            (max(1, int(base.width * sc)),
                             max(1, int(base.height * sc))), Image.BILINEAR)
                        ladder.append((fr.tobytes(), fr.size))

                sig = _open_rgba(sig_p)
                sig_t = None
                if sig is not None:
                    f = _fit(sig, int(w * 0.28), int(h * 0.16))
                    sig_t = (f.tobytes(), f.size)

                por = _open_rgba(por_p)
                por_t = None
                if por is not None:
                    f = _fit(por, int(w * 0.52), int(h * 0.86))
                    por_t = (f.tobytes(), f.size)

                glow_d = int(min(w, h) * 0.68)
                glow = _soft_dot(glow_d, colors.scale(main, 0.85), core=0.5)

                strip_h = max(8, int(h * 0.30)) + 6
                strip = _bar_strip(4, strip_h, accent)

                self._q.put((req, dict(
                    size=(w, h), photo=photo,
                    bg=(bg.tobytes(), bg.size),
                    ladder=ladder, sig=sig_t, por=por_t,
                    glow=(glow.tobytes(), glow.size),
                    strip=(strip.tobytes(), strip.size),
                    strip_h=strip_h)))
            except Exception:
                pass

        threading.Thread(target=work, name="stage-render", daemon=True).start()

    def _rgba(self, blob):
        if blob is None:
            return None
        data, size = blob
        return self.pg.image.frombuffer(data, size, "RGBA").convert_alpha()

    def _rgb(self, blob):
        data, size = blob
        return self.pg.image.frombuffer(data, size, "RGB").convert()

    def _poll(self) -> None:
        got = None
        got_req = 0
        try:
            while True:
                req, batch = self._q.get_nowait()
                if req == self._req:
                    got, got_req = batch, req
        except queue.Empty:
            pass
        if got is None or got["size"] != (self.W, self.H):
            return
        a = got

        def s_portrait():
            self._portrait = self._rgba(a["por"])

        def s_bg():
            self._bg = self._rgb(a["bg"])
            self._has_photo = bool(a["photo"])

        def s_ladder():
            self._ladder = [self._rgba(fr) for fr in a["ladder"][:1]]
            self._ladder_pending = a["ladder"][1:]

        def s_rest():
            self._sig = self._rgba(a["sig"])
            self._glow = self._rgb(a["glow"])
            self._strip = self._rgb(a["strip"])
            self._strip_h = a["strip_h"]
            self._dots = []
            self._np_card = None

        self._steps = [s_portrait, s_bg, s_ladder, s_rest]
        self._pending_seq = got_req

    def _step(self) -> None:
        if not self._steps:
            return
        fn = self._steps.pop(0)
        try:
            fn()
        except Exception:
            self._steps = []
        if not self._steps:
            self._applied = self._pending_seq

    # ----- state sync -------------------------------------------------------
    def _sync(self) -> None:
        v = self.feed.hero()
        if v.seq != self._seen_seq:
            self._seen_seq = v.seq
            self._visuals = v
            was_first = self._first_paint
            self._first_paint = False
            self._portrait = None            # never show the previous portrait
            self._request_render()
            if not was_first and self.cfg.stage.switch_anim:
                # The wipe starts once the new batch begins applying (portrait
                # first), so the panel carries the portrait from frame one.
                self._anim = {"phase": "arm", "t0": time.perf_counter()}

    # ----- frame ------------------------------------------------------------
    def _draw(self) -> None:
        dt = min(0.05, self.clock.get_time() / 1000.0) or (1.0 / self._fps)
        a = self._anim

        # Apply pending surfaces: one step per frame. While a wipe is arming we
        # apply the first step (portrait) and launch it; while the panel is
        # moving in we hold further steps (they'd swap the visible scene).
        if a is not None and a["phase"] == "arm":
            if self._steps:
                self._step()                       # portrait
                self._anim = {"phase": "in", "t0": time.perf_counter()}
            elif time.perf_counter() - a["t0"] > 4.0:
                # Renderer is unusually slow — skip the animation entirely
                # rather than ever sweeping a panel without the portrait.
                self._anim = None
        elif self._steps:
            if a is None or a["phase"] != "in":
                self._step()
        elif self._ladder_pending:
            for _ in range(2):
                if not self._ladder_pending:
                    break
                self._ladder.append(self._rgba(self._ladder_pending.pop(0)))

        st = self.cfg.stage
        v = self._visuals
        main = colors.hex_to_rgb(v.main_hex) if v else (29, 185, 84)
        accent = colors.hex_to_rgb(v.accent_hex) if v else (30, 215, 96)

        # Background.
        if self._bg is not None:
            self.screen.blit(self._bg, (0, 0))
        else:
            self.screen.fill(BG_BASE)

        # The beat envelope is already a clean per-kick pulse (instant attack,
        # ~150 ms decay, one fire per kick). Track it closely: fast on the way
        # up (2–3 frames), and follow the envelope down — the logo thumps in
        # lockstep with the bass drum.
        beat = self.feed.beat()
        rise = beat > self._pulse
        self._pulse += (beat - self._pulse) * (0.7 if rise else 0.5)
        self._glow_t += dt

        if st.particles:
            self._draw_particles(dt, accent, beat)

        # Breathing glow behind the logo (kicked gently by the beat).
        cx, cy = self.W // 2, int(self.H * 0.44)
        if self._glow is not None and st.glow > 0:
            breathe = 0.55 + 0.25 * math.sin(self._glow_t * 0.7)
            level = min(1.0, breathe + self._pulse * 0.6)
            self._glow.set_alpha(int(2.1 * st.glow * level))
            self.screen.blit(self._glow, (cx - self._glow.get_width() // 2,
                                          cy - self._glow.get_height() // 2),
                             special_flags=self.pg.BLEND_ADD)

        # Logo (pulse ladder) or the hero name as fallback.
        if self._ladder:
            depth = st.pulse / 100.0
            idx = int(self._pulse * (PULSE_STEPS - 1) * depth)
            idx = max(0, min(idx, len(self._ladder) - 1))
            fr = self._ladder[idx]
            self.screen.blit(fr, (cx - fr.get_width() // 2,
                                  cy - fr.get_height() // 2))
        elif v and v.name:
            t = self._text(v.name, max(26, int(self.H * 0.09)), colors.rgb_to_hex(main))
            self.screen.blit(t, (cx - t.get_width() // 2, cy - t.get_height() // 2))

        if self._sig is not None:
            pad = int(self.H * 0.045)
            self.screen.blit(self._sig, (self.W - pad - self._sig.get_width(), pad))

        if st.show_now_playing:
            self._draw_now_playing()

        self._draw_bars(accent)

        hint = self._text("double-click / F11 fullscreen · Esc exit",
                          max(10, int(self.H * 0.013)), "#565b62", bold=False)
        self.screen.blit(hint, (self.W - hint.get_width() - 12,
                                self.H - hint.get_height() - 6))

        if a is not None and a["phase"] != "arm":
            self._draw_wipe(main, accent)

    # ----- pieces -----------------------------------------------------------
    def _font(self, px: int, bold: bool):
        k = (px, bold)
        f = self._fonts.get(k)
        if f is None:
            f = self.pg.font.SysFont("Segoe UI", px, bold=bold)
            self._fonts[k] = f
        return f

    def _text(self, s: str, px: int, hex_color: str, bold: bool = True):
        k = (s, px, hex_color, bold)
        surf = self._texts.get(k)
        if surf is None:
            surf = self._font(px, bold).render(s, True, colors.hex_to_rgb(hex_color))
            if len(self._texts) > 200:
                self._texts.clear()
            self._texts[k] = surf
        return surf

    def _draw_particles(self, dt: float, accent, beat: float = 0.0) -> None:
        if not self._dots:
            for px in (24, 38, 54):
                im = _soft_dot(px, accent, core=0.5)
                self._dots.append(self._rgb((im.tobytes(), im.size)))
        # Music-reactive: particles brighten and drift faster on hits.
        glow = int(140 + 115 * min(1.0, beat * 1.3))
        for d in self._dots:
            d.set_alpha(glow)
        speed = 1.0 + beat * 2.5
        add = self.pg.BLEND_ADD
        for p in self._particles:
            p["y"] -= p["v"] * dt * speed
            p["ph"] += dt * (0.6 + beat * 2.0)
            if p["y"] < -0.05:
                p["y"] = 1.05
                p["x"] = random.random()
            dot = self._dots[int(p["s"] * 2.999)]
            x = (p["x"] + math.sin(p["ph"]) * 0.012) * self.W - dot.get_width() / 2
            self.screen.blit(dot, (x, p["y"] * self.H - dot.get_height() / 2),
                             special_flags=add)

    def _draw_now_playing(self) -> None:
        tr = self.feed.track()
        pad = int(self.H * 0.045)
        art = int(self.H * 0.135)
        card_w = int(self.W * 0.42)
        card_h = art + int(self.H * 0.03)

        # Translucent rounded card (pre-rendered per size).
        if self._np_card is None or self._np_card.get_size() != (card_w, card_h):
            s = self.pg.Surface((card_w, card_h), self.pg.SRCALPHA)
            self.pg.draw.rect(s, (10, 12, 16, 150), s.get_rect(),
                              border_radius=int(self.H * 0.022))
            self._np_card = s
        self.screen.blit(self._np_card, (pad - int(self.H * 0.015),
                                         pad - int(self.H * 0.015)))

        # Album art (rounded), with retry until the cache has the file.
        if tr.art_url != self._art_url and time.perf_counter() >= self._art_retry:
            self._art_retry = time.perf_counter() + 0.4
            if not tr.art_url:
                self._art_url = ""
                self._art_surf = None
            else:
                path = spotify.cached_art(tr.art_url)
                if path:
                    try:
                        img = Image.open(path).convert("RGB").resize(
                            (art, art), Image.LANCZOS)
                        img = _rounded(img, max(4, int(art * 0.12)))
                        self._art_surf = self._rgba((img.tobytes(), img.size))
                        self._art_url = tr.art_url
                    except Exception:
                        pass
        x, y = pad, pad
        if self._art_surf is not None:
            self.screen.blit(self._art_surf, (x, y))
        else:
            self.pg.draw.rect(self.screen, (18, 21, 26), (x, y, art, art),
                              border_radius=max(4, int(art * 0.12)))

        title = tr.title or "—"
        if title != self._last_title:
            self._last_title = title
            self._title_t = time.perf_counter()
        p = min(1.0, (time.perf_counter() - self._title_t) / 0.45)
        off = int((1.0 - _ease(p)) * self.H * 0.025)
        alpha = int(70 + 185 * _ease(p))

        tx = x + art + int(self.W * 0.015)
        ts = max(13, int(self.H * 0.030))
        t_s = self._text(title, ts, "#ffffff")
        a_s = self._text(tr.artist or "", max(11, int(self.H * 0.021)),
                         "#c4c9cd", bold=False)
        t_s.set_alpha(alpha)
        a_s.set_alpha(alpha)
        self.screen.blit(t_s, (tx, y + off))
        self.screen.blit(a_s, (tx, y + int(ts * 1.5) + off))
        t_s.set_alpha(255)
        a_s.set_alpha(255)

        # Progress bar with a glowing head dot.
        pb_w = int(self.W * 0.30)
        pb_h = max(3, int(self.H * 0.007))
        pb_y = y + art - pb_h - 2
        frac = (tr.live_progress_ms() / tr.duration_ms) if tr.duration_ms else 0.0
        frac = max(0.0, min(1.0, frac))
        self.pg.draw.rect(self.screen, (44, 48, 54), (tx, pb_y, pb_w, pb_h),
                          border_radius=pb_h // 2)
        if frac > 0:
            self.pg.draw.rect(self.screen, (240, 242, 244),
                              (tx, pb_y, int(pb_w * frac), pb_h),
                              border_radius=pb_h // 2)
            hx = tx + int(pb_w * frac)
            self.pg.draw.circle(self.screen, (255, 255, 255),
                                (hx, pb_y + pb_h // 2), pb_h + 1)

    def _draw_bars(self, accent) -> None:
        spec = self.feed.spectrum()
        n = len(self._levels)
        alpha = max(0.12, min(0.6, 0.5 * (60.0 / self._fps)))
        fall = 0.55 * (1.0 / self._fps) * 60 * 0.016
        margin = int(self.W * 0.04)
        usable = self.W - 2 * margin
        gap = max(1, int(usable / n * 0.28))
        bw = max(2, (usable - gap * (n - 1)) // n)
        base = int(self.H * 0.965)
        max_h = max(8, int(self.H * 0.30))
        cap = tuple(min(255, int(c * 1.5)) for c in accent)
        # Scale the gradient strip to the bar width ONCE per size change.
        strip = self._strip
        if strip is not None and strip.get_width() != bw:
            strip = self._strip = self.pg.transform.scale(strip, (bw, self._strip_h))
        for i in range(n):
            target = float(spec[i]) if spec is not None and i < len(spec) else 0.0
            target = max(target, 0.02)
            self._levels[i] += (target - self._levels[i]) * alpha
            self._peaks[i] = max(self._peaks[i] - fall, self._levels[i])
            x = margin + i * (bw + gap)
            bh = max(2, int(self._levels[i] * max_h))
            if strip is not None and self._strip_h >= bh:
                # Bottom-anchored slice of the pre-rendered gradient.
                self.screen.blit(strip, (x, base - bh),
                                 area=(0, self._strip_h - bh, bw, bh))
            else:
                self.pg.draw.rect(self.screen, accent, (x, base - bh, bw, bh))
            py = base - max(2, int(self._peaks[i] * max_h))
            self.pg.draw.rect(self.screen, cap, (x, py - 3, bw, 2))

    def _draw_wipe(self, main, accent) -> None:
        a = self._anim
        W, H = self.W, self.H
        s = max(24, int(W * SLANT))
        now = time.perf_counter()
        phase = a["phase"]

        if phase == "in":
            p = (now - a["t0"]) / T_IN
            if p >= 1.0:
                self._anim = {"phase": "hold", "t0": now}
                x = -s
            else:
                x = int(W - (W + s) * _ease(p))
        elif phase == "hold":
            x = -s
            if self._applied == self._req:      # scene fully swapped behind us
                self._anim = {"phase": "show", "t0": now}
        elif phase == "show":
            x = -s
            if (now - a["t0"]) >= T_SHOW:
                self._anim = {"phase": "out", "t0": now}
        else:                                   # out
            p = (now - a["t0"]) / T_OUT
            if p >= 1.0:
                self._anim = None
                return
            x = int(-s - (W + 2 * s) * _ease(p))

        panel = colors.scale(main, 0.28)
        self.pg.draw.polygon(self.screen, panel,
                             [(x + s, 0), (x + W + 2 * s, 0),
                              (x + W + s, H), (x, H)])
        e = max(4, int(W * 0.008))
        self.pg.draw.polygon(self.screen, accent,
                             [(x + s, 0), (x + s + e, 0), (x + e, H), (x, H)])

        pcx = x + s + W // 2
        drift = 0
        if phase == "show":
            drift = int(-H * 0.015 * _ease((now - a["t0"]) / T_SHOW))
        if self._portrait is not None:
            self.screen.blit(self._portrait,
                             (pcx - self._portrait.get_width() // 2,
                              H // 2 - self._portrait.get_height() // 2 + drift))
        name = (self._visuals.name if self._visuals else "").upper()
        if name:
            big = self._text(name, max(28, int(H * 0.08)), "#ffffff")
            sh = self._text(name, max(28, int(H * 0.08)), "#000000")
            ny = int(H * 0.80)
            self.screen.blit(sh, (pcx - big.get_width() // 2 + 3, ny + 3))
            self.screen.blit(big, (pcx - big.get_width() // 2, ny))
