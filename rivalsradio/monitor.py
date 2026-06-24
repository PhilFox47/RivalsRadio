"""The detection loop: capture -> recognize -> debounce -> switch playlist.

Runs on a background thread. Communicates with the UI purely through callbacks
so it never touches Tkinter widgets directly.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .capture import ScreenGrabber
from .config import Config
from .recognizer import HeroRecognizer
from .spotify_controller import SpotifyController


# Callback signatures: (message: str) for logs/status, (hero: str) for changes.
LogFn = Callable[[str], None]
HeroFn = Callable[[Optional[str]], None]


class Monitor:
    def __init__(
        self,
        cfg: Config,
        spotify: SpotifyController,
        on_log: LogFn,
        on_hero: HeroFn,
    ) -> None:
        self.cfg = cfg
        self.spotify = spotify
        self.on_log = on_log
        self.on_hero = on_hero

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._current_hero: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        if not self.cfg.capture_region.is_valid():
            self.on_log("Cannot start: capture region is not set (see Settings).")
            return
        refs = self.cfg.heroes_with_references()
        if not refs:
            self.on_log("Cannot start: no heroes have calibrated references yet.")
            return
        self._stop.clear()
        self._current_hero = None
        self._thread = threading.Thread(target=self._run, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    # ------------------------------------------------------------------
    def _run(self) -> None:
        recognizer = HeroRecognizer()
        recognizer.load_references(self.cfg.heroes_with_references())
        self.on_log(
            f"Monitoring started — watching for {recognizer.hero_count} "
            f"calibrated hero(es)."
        )

        grabber: Optional[ScreenGrabber] = None
        try:
            grabber = ScreenGrabber()
        except Exception as exc:  # pragma: no cover - depends on display
            self.on_log(f"Failed to initialise screen capture: {exc}")
            return

        pending_hero: Optional[str] = None
        pending_count = 0

        try:
            while not self._stop.is_set():
                frame = grabber.grab(self.cfg.capture_region)
                if frame is not None:
                    name, score = recognizer.best_match(frame)
                    if name is not None and score >= self.cfg.match_threshold:
                        # Debounce: require N consecutive reads of the same hero
                        # before committing to a switch. Keeps menus / kill-cams
                        # of other heroes from yanking the music around.
                        if name == pending_hero:
                            pending_count += 1
                        else:
                            pending_hero = name
                            pending_count = 1

                        if (
                            pending_count >= self.cfg.confirm_count
                            and name != self._current_hero
                        ):
                            self._switch_hero(name)
                    # Below-threshold reads (menus, loading) are ignored so the
                    # current playlist keeps playing.

                self._stop.wait(self.cfg.poll_interval)
        finally:
            if grabber:
                grabber.close()
            self.on_log("Monitoring stopped.")

    def _switch_hero(self, hero: str) -> None:
        self._current_hero = hero
        self.on_hero(hero)
        hero_cfg = self.cfg.heroes.get(hero)
        playlist = hero_cfg.playlist_uri if hero_cfg else ""
        if not playlist:
            self.on_log(f"Detected {hero}, but no playlist is mapped to it.")
            return
        if not self.spotify.connected:
            self.on_log(f"Detected {hero}, but Spotify is not connected.")
            return
        try:
            self.spotify.play_playlist(playlist)
            self.on_log(f"Detected {hero} → switched to its playlist.")
        except Exception as exc:
            self.on_log(f"Detected {hero}, but playback failed: {exc}")
