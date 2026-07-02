"""Thread-safe live state shared between the app logic and the Stage renderer."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Track:
    title: str = ""
    artist: str = ""
    art_url: str = ""
    progress_ms: int = 0
    duration_ms: int = 0
    playing: bool = False
    fetched_at: float = field(default_factory=time.time)

    def live_progress_ms(self) -> int:
        """Interpolated progress that keeps moving between Spotify polls."""
        if not self.playing or self.duration_ms <= 0:
            return min(self.progress_ms, self.duration_ms)
        elapsed = (time.time() - self.fetched_at) * 1000.0
        return int(min(self.progress_ms + elapsed, self.duration_ms))


@dataclass
class HeroVisuals:
    name: str = ""
    logo: Optional[str] = None          # absolute art paths (or None)
    portrait: Optional[str] = None
    signature: Optional[str] = None
    background: Optional[str] = None
    main_hex: str = "#1DB954"
    accent_hex: str = "#1ed760"
    seq: int = 0                        # bumped per change (cheap comparisons)


class Feed:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hero = HeroVisuals()
        self._track = Track()
        self._audio = None              # attached AudioEngine (or None)

    def attach_audio(self, audio) -> None:
        self._audio = audio

    # ----- hero ---------------------------------------------------------
    def set_hero(self, visuals: HeroVisuals) -> None:
        with self._lock:
            visuals.seq = self._hero.seq + 1
            self._hero = visuals

    def hero(self) -> HeroVisuals:
        with self._lock:
            return self._hero

    # ----- track --------------------------------------------------------
    def set_track(self, track: Track) -> None:
        with self._lock:
            self._track = track

    def track(self) -> Track:
        with self._lock:
            return self._track

    # ----- audio --------------------------------------------------------
    def spectrum(self):
        if self._audio is not None:
            return self._audio.spectrum()
        return None

    def beat(self) -> float:
        if self._audio is not None:
            return self._audio.beat()
        return 0.0
