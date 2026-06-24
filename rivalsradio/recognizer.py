"""Hero recognition by matching the live HUD region against saved references.

We use normalized cross-correlation (OpenCV ``TM_CCOEFF_NORMED``) on grayscale,
fixed-size crops. References are recorded during calibration (one PNG per hero),
so no copyrighted game art ships with the app.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

# All crops are normalized to this size before comparison. Small enough to be
# fast, large enough to keep the ability-icon detail that distinguishes heroes.
STANDARD_SIZE = (128, 128)


def _normalize(frame_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, STANDARD_SIZE, interpolation=cv2.INTER_AREA)


def save_reference(frame_bgr: np.ndarray, path: str) -> None:
    """Persist a captured HUD crop as a reference PNG."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, frame_bgr)


class HeroRecognizer:
    """Holds normalized reference templates and scores live frames against them."""

    def __init__(self) -> None:
        self._templates: Dict[str, np.ndarray] = {}

    def load_references(self, references: Dict[str, str]) -> None:
        """Load hero -> reference-image-path mappings into memory."""
        self._templates.clear()
        for hero, path in references.items():
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is None:
                continue
            self._templates[hero] = _normalize(img)

    @property
    def hero_count(self) -> int:
        return len(self._templates)

    def best_match(self, frame_bgr: np.ndarray) -> Tuple[Optional[str], float]:
        """Return (hero_name, score) for the best matching reference.

        Score is in roughly [-1, 1]; higher means a closer match. Returns
        (None, -1.0) when no references are loaded.
        """
        if not self._templates:
            return None, -1.0
        target = _normalize(frame_bgr)
        best_name: Optional[str] = None
        best_score = -1.0
        for hero, template in self._templates.items():
            # Same-size inputs -> matchTemplate yields a single 1x1 result.
            result = cv2.matchTemplate(target, template, cv2.TM_CCOEFF_NORMED)
            score = float(result[0][0])
            if score > best_score:
                best_score = score
                best_name = hero
        return best_name, best_score
