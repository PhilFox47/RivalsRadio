"""Locate the Marvel Rivals window and suggest a HUD capture region.

Uses pygetwindow (Windows/macOS). Guarded so the rest of the app still imports
on systems without it.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .config import CaptureRegion


def backend_available() -> bool:
    try:
        import pygetwindow  # noqa: F401
        return True
    except Exception:
        return False


def find_game_rect(title_substring: str) -> Optional[Tuple[int, int, int, int]]:
    """Return (left, top, width, height) of the first matching window, or None."""
    try:
        import pygetwindow as gw
    except Exception:
        return None
    try:
        wins = gw.getAllWindows()
    except Exception:
        return None
    needle = title_substring.lower()
    for win in wins:
        title = (getattr(win, "title", "") or "").lower()
        if needle in title and getattr(win, "width", 0) > 0:
            return (int(win.left), int(win.top), int(win.width), int(win.height))
    return None


def suggest_hud_region(rect: Tuple[int, int, int, int]) -> CaptureRegion:
    """Heuristic HUD region: the bottom-right ability-icon cluster.

    This is a starting point — the user can fine-tune it with the region
    selector and Test detection.
    """
    left, top, width, height = rect
    # Bottom-right area where ultimate/ability icons live, as fractions.
    rx = left + int(width * 0.78)
    ry = top + int(height * 0.82)
    rw = int(width * 0.20)
    rh = int(height * 0.16)
    return CaptureRegion(left=rx, top=ry, width=rw, height=rh)
