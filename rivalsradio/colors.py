"""Colour utilities and automatic per-hero palette extraction.

Each hero has a Main colour (tints the white logo / drives the glow) and an
Accent colour (visualizer bars). When not set manually, both are extracted
from the hero's portrait: Main is the most vibrant dominant colour, Accent is
the strongest *different-hue* colour so the pair reads as a real palette.
"""

from __future__ import annotations

import colorsys
from typing import Optional, Tuple

RGB = Tuple[int, int, int]
DEFAULT_MAIN = "#1DB954"
DEFAULT_ACCENT = "#1ed760"


def is_hex(value: str) -> bool:
    v = (value or "").lstrip("#")
    if len(v) != 6:
        return False
    try:
        int(v, 16)
        return True
    except ValueError:
        return False


def hex_to_rgb(value: str) -> RGB:
    v = value.lstrip("#")
    if len(v) != 6:
        return (29, 185, 84)
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def rgb_to_hex(rgb: RGB) -> str:
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def scale(rgb: RGB, factor: float) -> RGB:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)  # type: ignore


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))  # type: ignore


def readable_ink(hex_value: str) -> str:
    """Black or white text for legibility on the given colour."""
    r, g, b = hex_to_rgb(hex_value)
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def _bucketize(path: str):
    """Saturation-weighted colour buckets of an image; [] on any problem."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGBA")
    except Exception:
        return []
    im.thumbnail((96, 96))
    buckets: dict = {}
    for r, g, b, a in im.getdata():
        if a < 160:
            continue
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if v < 0.18 or s < 0.22:
            continue                      # skip near-black / near-grey
        key = int(h * 18)                 # 20°-wide hue buckets
        w = (s ** 2) * v
        e = buckets.get(key)
        if e is None:
            buckets[key] = [w, r * w, g * w, b * w]
        else:
            e[0] += w
            e[1] += r * w
            e[2] += g * w
            e[3] += b * w
    out = []
    for key, (w, rs, gs, bs) in buckets.items():
        out.append((w, key, (rs / w, gs / w, bs / w)))
    out.sort(reverse=True)
    return out


def _punch(rgb) -> RGB:
    """Nudge a colour toward display punchiness (min saturation/brightness)."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.55), max(v, 0.66))
    return (int(r * 255), int(g * 255), int(b * 255))


def palette_from_image(path: Optional[str]) -> Tuple[str, str]:
    """(main_hex, accent_hex) extracted from an image; sane defaults if not."""
    buckets = _bucketize(path) if path else []
    if not buckets:
        return DEFAULT_MAIN, DEFAULT_ACCENT
    main = _punch(buckets[0][2])
    main_hue = buckets[0][1]
    accent_rgb = None
    for w, hue, rgb in buckets[1:]:
        if min(abs(hue - main_hue), 18 - abs(hue - main_hue)) >= 3:
            accent_rgb = _punch(rgb)
            break
    if accent_rgb is None:
        accent_rgb = scale(main, 1.35)    # lighter shade of main
    return rgb_to_hex(main), rgb_to_hex(accent_rgb)
