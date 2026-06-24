"""Coordinates a hero source with Spotify playback.

The monitor is source-agnostic: it asks a ``HeroSource`` (screen capture or the
Overwolf GEP bridge) to report the current hero, then switches the Spotify
playlist on each confirmed hero change.
"""

from __future__ import annotations

from typing import Callable, Optional

from .config import Config
from .hero_source import make_hero_source, HeroSource
from .spotify_controller import SpotifyController

LogFn = Callable[[str], None]
HeroFn = Callable[[Optional[str]], None]


class Monitor:
    def __init__(self, cfg: Config, spotify: SpotifyController,
                 on_log: LogFn, on_hero: HeroFn) -> None:
        self.cfg = cfg
        self.spotify = spotify
        self.on_log = on_log
        self.on_hero = on_hero

        self._source: Optional[HeroSource] = None
        self._current_hero: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._source is not None

    def start(self) -> None:
        if self.running:
            return
        source = make_hero_source(self.cfg)
        if not source.available:
            self.on_log(f"Cannot start ({source.name}): {source.unavailable_reason()}")
            return
        self._current_hero = None
        self._source = source
        source.start(self._on_candidate, self.on_log)
        self.on_log(f"Monitoring started using the '{source.name}' hero source.")

    def stop(self) -> None:
        if self._source:
            self._source.stop()
            self._source = None

    def _on_candidate(self, hero: str) -> None:
        """A source reported a (confident) current hero."""
        hero = self.cfg.canonical_hero(hero)
        if hero == self._current_hero:
            return
        self._switch_hero(hero)

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
