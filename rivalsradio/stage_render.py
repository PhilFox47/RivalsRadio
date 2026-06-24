"""Pure-PIL rendering for the Stage background (no Tkinter).

Kept separate from ``stage.py`` so the visual composition can be unit-tested
and used to generate previews on machines without a display.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import theming

BG_BOTTOM = (5, 6, 10)


def screen_blend(base: Image.Image, glow: Image.Image) -> Image.Image:
    """Screen-blend a glow over a base image (lightens, never darkens)."""
    return ImageChops.screen(base.convert("RGB"), glow.convert("RGB"))


def render_background(w: int, h: int, accent: Tuple[int, int, int],
                      avatar: Optional[Image.Image]) -> Image.Image:
    """Build the static Stage background: gradient + accent glow + avatar."""
    dark = theming.scale(accent, 0.16)
    top_col = theming.mix(BG_BOTTOM, dark, 0.9)

    # Vertical gradient (brighter, accent-tinted at the top).
    bg = Image.new("RGB", (w, h), BG_BOTTOM)
    grad = Image.new("L", (1, h))
    for y in range(h):
        grad.putpixel((0, y), int(255 * (1 - y / h)))
    grad = grad.resize((w, h))
    bg = Image.composite(Image.new("RGB", (w, h), top_col), bg, grad)

    # Soft accent glow behind the avatar, screen-blended so it only lightens.
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = w // 2, int(h * 0.52)
    rr = max(1, int(min(w, h) * 0.42))
    gd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=theming.scale(accent, 0.5))
    glow = glow.filter(ImageFilter.GaussianBlur(max(1, rr // 2)))
    bg = screen_blend(bg, glow)

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
