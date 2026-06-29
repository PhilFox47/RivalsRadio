"""Coordinates a hero source with Spotify playback.

The monitor is source-agnostic: it asks a ``HeroSource`` (the native Overwolf
app or the ow-electron GEP bridge) to report the current hero, then switches
the Spotify playlist on each confirmed hero change.
"""

from __future__ import annotations

from typing import Callable, Optional

from .config import Config, HeroConfig
from .hero_source import GepHeroSource, NativeHeroSource, HeroSource
from .spotify_controller import SpotifyController

LogFn = Callable[[str], None]
HeroFn = Callable[[Optional[str]], None]
EventFn = Callable[[dict], None]


class Monitor:
    def __init__(self, cfg: Config, spotify: SpotifyController,
                 on_log: LogFn, on_hero: HeroFn,
                 on_event: Optional[EventFn] = None) -> None:
        self.cfg = cfg
        self.spotify = spotify
        self.on_log = on_log
        self.on_hero = on_hero
        self.on_event = on_event

        self._source: Optional[HeroSource] = None
        self._current_hero: Optional[str] = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._current_hero = None
        self._running = True
        mode = self.cfg.hero_source

        if mode == "gep":
            self._start_source(GepHeroSource(self.cfg))
        else:  # "native" (default)
            self._start_source(NativeHeroSource(self.cfg))

    def _start_source(self, source: HeroSource) -> None:
        if not source.available:
            self.on_log(f"Cannot start ({source.name}): {source.unavailable_reason()}")
            self._running = False
            return
        self._source = source
        source.start(self._on_candidate, self.on_log, self._on_source_failed, self.on_event)
        self.on_log(f"Monitoring started using the '{source.name}' hero source.")

    def _on_source_failed(self) -> None:
        """A source died or was unavailable."""
        if not self._running:
            return
        self._running = False

    def stop(self) -> None:
        self._running = False
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
        # Self-expanding roster: if the game reports a hero we've never seen
        # (e.g. a newly released character), add it so it shows up in the
        # Heroes tab ready for a playlist — no manual add or app update needed.
        if hero not in self.cfg.heroes:
            self.cfg.heroes[hero] = HeroConfig()
            self.cfg.save()
            self.on_log(f"New hero '{hero}' added to your roster — "
                        f"map a playlist for it in the Heroes tab.")
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
