"""Shared, thread-safe presentation state for the Stage and web overlay.

Both the Tkinter Stage window and the OBS web overlay read from a single
``StageState`` so they always show the same hero, accent, track and spectrum.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class TrackInfo:
    title: str = ""
    artist: str = ""
    album_art_url: str = ""
    progress_ms: int = 0
    duration_ms: int = 0
    is_playing: bool = False
    fetched_at: float = field(default_factory=time.time)

    def live_progress_ms(self) -> int:
        """Interpolated progress, advancing between polls while playing."""
        if not self.is_playing or self.duration_ms <= 0:
            return min(self.progress_ms, self.duration_ms)
        elapsed = (time.time() - self.fetched_at) * 1000.0
        return int(min(self.progress_ms + elapsed, self.duration_ms))


class StageState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.hero: Optional[str] = None
        self.avatar_path: Optional[str] = None
        self.logo_path: Optional[str] = None
        self.signature_path: Optional[str] = None
        self.accent_hex: str = "#1DB954"
        self.main_hex: str = "#0d3a22"
        self.track = TrackInfo()
        self._visualizer = None  # set via attach_visualizer

    def attach_visualizer(self, visualizer) -> None:
        self._visualizer = visualizer

    def set_hero(self, hero: str, avatar_path: Optional[str], accent_hex: str,
                 logo_path: Optional[str] = None,
                 signature_path: Optional[str] = None,
                 main_hex: Optional[str] = None) -> None:
        with self._lock:
            self.hero = hero
            self.avatar_path = avatar_path
            self.logo_path = logo_path
            self.signature_path = signature_path
            if accent_hex:
                self.accent_hex = accent_hex
            if main_hex:
                self.main_hex = main_hex

    def set_track(self, track: TrackInfo) -> None:
        with self._lock:
            self.track = track

    def snapshot(self) -> dict:
        """Plain-dict snapshot for the web overlay's JSON endpoint."""
        with self._lock:
            t = self.track
            return {
                "hero": self.hero,
                "accent": self.accent_hex,
                "main": self.main_hex,
                "track": {
                    "title": t.title,
                    "artist": t.artist,
                    "album_art_url": t.album_art_url,
                    "progress_ms": t.live_progress_ms(),
                    "duration_ms": t.duration_ms,
                    "is_playing": t.is_playing,
                },
                "spectrum": self.spectrum().tolist(),
            }

    def spectrum(self) -> np.ndarray:
        if self._visualizer is not None:
            return self._visualizer.get_spectrum()
        return np.zeros(1, dtype=np.float32)
