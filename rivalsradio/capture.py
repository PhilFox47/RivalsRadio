"""Screen capture helpers built on mss.

mss grabbers are not safe to share across threads, so each consumer creates its
own ``ScreenGrabber`` instance on the thread where it will be used.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .config import CaptureRegion


class ScreenGrabber:
    def __init__(self) -> None:
        # Imported lazily so the module can be imported on machines without a
        # display (e.g. CI) without immediately failing.
        import mss

        self._sct = mss.mss()

    def grab(self, region: CaptureRegion) -> Optional[np.ndarray]:
        """Capture ``region`` and return it as a BGR numpy array, or None."""
        if not region.is_valid():
            return None
        monitor = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }
        shot = self._sct.grab(monitor)
        # mss returns BGRA; drop the alpha channel -> BGR (what OpenCV expects).
        frame = np.asarray(shot)[:, :, :3]
        return frame

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass
