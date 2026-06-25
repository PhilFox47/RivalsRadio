"""Pure-PIL rendering for the Stage background (no Tkinter).

Kept separate from ``stage.py`` so the visual composition can be unit-tested
and used to generate previews on machines without a display.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import theming

BG_BOTTOM = (5, 6, 10)
# The gradient + glow are smooth/blurry, so they can be rendered at a capped
# resolution and upscaled with no visible loss. This keeps large/4K fullscreen
# fast; only the sharp avatar is composited at full resolution.
RENDER_CAP = 1280


def screen_blend(base: Image.Image, glow: Image.Image) -> Image.Image:
    """Screen-blend a glow over a base image (lightens, never darkens)."""
    return ImageChops.screen(base.convert("RGB"), glow.convert("RGB"))


def render_background(w: int, h: int, accent: Tuple[int, int, int],
                      avatar: Optional[Image.Image]) -> Image.Image:
    """Build the static Stage background: gradient + accent glow + avatar."""
    dark = theming.scale(accent, 0.16)
    top_col = theming.mix(BG_BOTTOM, dark, 0.9)

    # Work out a capped render size for the smooth layers.
    if max(w, h) > RENDER_CAP:
        s = RENDER_CAP / float(max(w, h))
        rw, rh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    else:
        rw, rh = w, h

    # Vertical gradient (brighter, accent-tinted at the top), vectorised.
    t = np.linspace(1.0, 0.0, rh, dtype=np.float32)            # 1 at top → 0 at bottom
    top = np.array(top_col, dtype=np.float32)
    bot = np.array(BG_BOTTOM, dtype=np.float32)
    col = bot[None, :] * (1.0 - t)[:, None] + top[None, :] * t[:, None]   # (rh, 3)
    grad_arr = np.repeat(col[:, None, :], rw, axis=1).astype(np.uint8)    # (rh, rw, 3)
    bg = Image.fromarray(grad_arr, "RGB")

    # Soft accent glow behind the avatar, screen-blended so it only lightens.
    glow = Image.new("RGB", (rw, rh), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = rw // 2, int(rh * 0.52)
    rr = max(1, int(min(rw, rh) * 0.42))
    gd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=theming.scale(accent, 0.5))
    glow = glow.filter(ImageFilter.GaussianBlur(max(1, rr // 2)))
    bg = screen_blend(bg, glow)

    # Upscale the smooth layers to the real size before the sharp avatar.
    if (rw, rh) != (w, h):
        bg = bg.resize((w, h), Image.BILINEAR)

    # Composite the transparent avatar, centred and scaled to fit.
    if avatar is not None:
        target_h = int(h * 0.82)
        ratio = target_h / avatar.height
        target_w = max(1, int(avatar.width * ratio))
        if target_w > int(w * 0.9):
            target_w = int(w * 0.9)
            target_h = max(1, int(avatar.height * (target_w / avatar.width)))
        resized = avatar.resize((target_w, target_h), Image.LANCZOS)
        px = (w - target_w) // 2
        py = max(0, int(h * 0.50) - target_h // 2 + int(h * 0.04))
        bg = bg.convert("RGBA")
        bg.alpha_composite(resized, (px, py))
        bg = bg.convert("RGB")

    return bg
