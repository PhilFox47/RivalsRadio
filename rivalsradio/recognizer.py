"""Hero recognition by matching the live HUD region against saved references.

We use normalized cross-correlation (OpenCV ``TM_CCOEFF_NORMED``) on grayscale
crops. References are recorded during calibration (one PNG per hero), so no
copyrighted game art ships with the app.

Robustness without extra dependencies:
- The reference template is matched against a slightly larger search window so
  small positional drift of the HUD doesn't tank the score (translation
  tolerance).
- The template is tried at a few scales so a different game resolution doesn't
  require re-calibrating every hero (scale tolerance).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# Reference templates are normalized to this size before comparison. Small
# enough to be fast, large enough to keep the detail that distinguishes heroes.
TEMPLATE_SIZE = 128
# The live frame is normalized a bit larger so the template can slide inside it.
SEARCH_SIZE = 152
# Scales (relative to TEMPLATE_SIZE) tried for resolution/scale tolerance.
SCALES = (0.88, 1.0, 1.14)


def _to_gray(frame_bgr: np.ndarray, size: int) -> np.ndarray:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)


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
            self._templates[hero] = _to_gray(img, TEMPLATE_SIZE)

    @property
    def hero_count(self) -> int:
        return len(self._templates)

    def _score(self, search: np.ndarray, template: np.ndarray) -> float:
        """Best correlation of ``template`` against ``search`` over scales."""
        best = -1.0
        for scale in SCALES:
            side = max(8, int(round(TEMPLATE_SIZE * scale)))
            if side >= search.shape[0]:
                side = search.shape[0] - 1
            tpl = cv2.resize(template, (side, side), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
            best = max(best, float(result.max()))
        return best

    def rank_matches(self, frame_bgr: np.ndarray) -> List[Tuple[str, float]]:
        """Return [(hero, score), ...] sorted best-first for all references."""
        if not self._templates:
            return []
        search = _to_gray(frame_bgr, SEARCH_SIZE)
        scored = [
            (hero, self._score(search, tpl))
            for hero, tpl in self._templates.items()
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def best_match(self, frame_bgr: np.ndarray) -> Tuple[Optional[str], float]:
        """Return (hero_name, score) for the best matching reference.

        Score is in roughly [-1, 1]; higher means a closer match. Returns
        (None, -1.0) when no references are loaded.
        """
        ranked = self.rank_matches(frame_bgr)
        if not ranked:
            return None, -1.0
        return ranked[0]
