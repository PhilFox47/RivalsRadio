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
    """Saturation-weighted colour buckets of an image; [] on any problem.

    NumPy-vectorised: the HSV conversion and hue bucketing run as a handful of
    array ops over the 96x96 thumbnail instead of a per-pixel Python/colorsys
    loop (~9k iterations per image). Same maths, ~100x cheaper, so extracting
    the whole roster's palettes off the UI thread is cheap. float64 throughout
    to match colorsys bit-for-bit at bucket boundaries.
    """
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(path).convert("RGBA")
    except Exception:
        return []
    im.thumbnail((96, 96))
    arr = np.asarray(im, dtype=np.float64)
    if arr.ndim != 3 or arr.shape[2] != 4:
        return []
    px = arr.reshape(-1, 4)
    r, g, b, a = px[:, 0], px[:, 1], px[:, 2], px[:, 3]

    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    rangec = maxc - minc
    v = maxc / 255.0
    s = np.where(maxc > 0.0, rangec / np.where(maxc > 0.0, maxc, 1.0), 0.0)

    safe = np.where(rangec > 0.0, rangec, 1.0)     # grey pixels get filtered by s
    rc = (maxc - r) / safe
    gc = (maxc - g) / safe
    bc = (maxc - b) / safe
    h = np.where(r == maxc, bc - gc,
                 np.where(g == maxc, 2.0 + rc - bc, 4.0 + gc - rc))
    h = np.mod(h / 6.0, 1.0)

    mask = (a >= 160) & (v >= 0.18) & (s >= 0.22)   # drop near-black / near-grey
    if not mask.any():
        return []

    key = (h[mask] * 18).astype(np.int64)           # 20-degree hue buckets
    w = (s[mask] ** 2) * v[mask]
    rw, gw, bw = r[mask] * w, g[mask] * w, b[mask] * w

    n = 18
    tw = np.bincount(key, weights=w, minlength=n)
    tr = np.bincount(key, weights=rw, minlength=n)
    tg = np.bincount(key, weights=gw, minlength=n)
    tb = np.bincount(key, weights=bw, minlength=n)

    out = []
    for k in range(n):
        wk = tw[k]
        if wk > 0.0:
            out.append((float(wk), k,
                        (tr[k] / wk, tg[k] / wk, tb[k] / wk)))
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
