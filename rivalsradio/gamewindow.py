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
    """Heuristic HUD region: the bottom-left hero portrait.

    The portrait (your hero's face, lower-left) is a more stable anchor than the
    right-side ability icons, which change with cooldowns / ammo / ult charge.
    This is a starting box the user can fine-tune with the region selector and
    Test detection; keep it tight on the face, away from the frame edges (which
    can glow when your ultimate is ready).
    """
    left, top, width, height = rect
    rx = left + int(width * 0.02)
    ry = top + int(height * 0.88)
    rw = int(width * 0.07)
    rh = int(height * 0.11)
    return CaptureRegion(left=rx, top=ry, width=rw, height=rh)
