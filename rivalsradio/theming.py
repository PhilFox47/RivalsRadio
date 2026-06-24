"""Accent-colour extraction and small colour helpers for the Stage view."""

from __future__ import annotations

import colorsys
from typing import Tuple

DEFAULT_ACCENT = "#1DB954"  # Spotify green, used as a fallback


def hex_to_rgb(value: str) -> Tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        return (29, 185, 84)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def mix(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    """Linear blend: t=0 -> c1, t=1 -> c2."""
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))  # type: ignore


def scale(rgb: Tuple[int, int, int], factor: float) -> Tuple[int, int, int]:
    """Brighten (>1) or darken (<1) a colour."""
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)  # type: ignore


def is_valid_hex(value: str) -> bool:
    v = value.lstrip("#")
    if len(v) != 6:
        return False
    try:
        int(v, 16)
        return True
    except ValueError:
        return False


def extract_accent(image_path: str) -> str:
    """Pick a vibrant accent colour from a (possibly transparent) image.

    Considers only sufficiently opaque, non-dark pixels and weights buckets by
    saturation so the result is a lively colour rather than a muddy average.
    Returns a "#RRGGBB" string, falling back to DEFAULT_ACCENT on any problem.
    """
    try:
        from PIL import Image
    except Exception:
        return DEFAULT_ACCENT

    try:
        im = Image.open(image_path).convert("RGBA")
    except Exception:
        return DEFAULT_ACCENT

    im.thumbnail((128, 128))
    px = im.load()
    if px is None:
        return DEFAULT_ACCENT

    # bucket -> [weight, r_sum, g_sum, b_sum, n]
    buckets: dict = {}
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a < 160:
                continue
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if v < 0.18 or s < 0.18:
                continue  # skip near-black and near-grey pixels
            weight = (s ** 2) * v
            key = (r // 24, g // 24, b // 24)
            entry = buckets.get(key)
            if entry is None:
                buckets[key] = [weight, r, g, b, 1]
            else:
                entry[0] += weight
                entry[1] += r
                entry[2] += g
                entry[3] += b
                entry[4] += 1

    if not buckets:
        return DEFAULT_ACCENT

    best = max(buckets.values(), key=lambda e: e[0])
    n = best[4]
    avg = (best[1] / n, best[2] / n, best[3] / n)

    # Nudge toward a punchy display colour: ensure decent saturation/brightness.
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in avg))
    s = max(s, 0.55)
    v = max(v, 0.65)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return rgb_to_hex((r * 255, g * 255, b * 255))
