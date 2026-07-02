"""The conductor wires everything together:

detection → roster (auto-extending) → Spotify playlist → Stage feed.

It owns the config, the Spotify client, the detector and the now-playing
poller, and is the single place a hero change (detected or manual) flows
through.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import colors, spotify as spotify_mod
from .config import Config, Hero
from .detector import Detector
from .feed import Feed, HeroVisuals, Track
from .spotify import Spotify

LogFn = Callable[[str], None]


class Conductor:
    def __init__(self, cfg: Config, feed: Feed, on_log: LogFn,
                 on_hero: Callable[[str], None],
                 on_game: Callable[[bool], None],
                 on_roster_change: Callable[[], None]) -> None:
        self.cfg = cfg
        self.feed = feed
        self.on_log = on_log
        self.on_hero_ui = on_hero
        self.on_game_ui = on_game
        self.on_roster_change = on_roster_change

        self.spotify = Spotify(cfg.spotify)
        self.detector = Detector(cfg.detector_port,
                                 on_hero=self._hero_detected,
                                 on_game=on_game,
                                 on_log=on_log,
                                 on_ult=feed.set_ult)
        self.current_hero: Optional[str] = None
        self._palette_cache: dict = {}
        self._np_stop = threading.Event()
        self._np_thread: Optional[threading.Thread] = None

    # ----- lifecycle ------------------------------------------------------
    def start(self) -> None:
        self.detector.start()
        if self._np_thread is None or not self._np_thread.is_alive():
            self._np_stop.clear()
            self._np_thread = threading.Thread(
                target=self._poll_now_playing, name="nowplaying", daemon=True)
            self._np_thread.start()

    def stop(self) -> None:
        self.detector.stop()
        self._np_stop.set()

    def connect_spotify_async(self, done: Optional[Callable[[bool, str], None]] = None) -> None:
        """Connect on a worker thread (auth blocks on the browser redirect)."""
        def work() -> None:
            try:
                self.spotify.connect()
                self.on_log("Spotify connected.")
                if done:
                    done(True, "")
            except Exception as exc:
                self.on_log(f"Spotify connection failed: {exc}")
                if done:
                    done(False, str(exc))
        threading.Thread(target=work, name="spotify-connect", daemon=True).start()

    # ----- hero flow --------------------------------------------------
    def _hero_detected(self, name: str) -> None:
        self.set_hero(name, source="detected")

    def set_hero(self, name: str, source: str = "manual") -> None:
        name = self.cfg.canonical(name)
        if name == self.current_hero and source == "detected":
            return
        self.current_hero = name
        # Auto-extending roster: unknown heroes get an entry immediately.
        if name not in self.cfg.heroes:
            self.cfg.heroes[name] = Hero()
            self.cfg.save()
            self.on_log(f"New hero '{name}' added to the roster — set a "
                        f"playlist for it in the Heroes tab.")
            self.on_roster_change()
        self.on_hero_ui(name)
        self.feed.set_ult(0)            # new hero starts uncharged
        self.feed.set_hero(self.visuals_for(name))
        self._play_for(name, source)

    def _play_for(self, name: str, source: str) -> None:
        hero = self.cfg.heroes.get(name)
        playlist = hero.playlist if hero else ""
        if not playlist:
            self.on_log(f"{name}: no playlist mapped.")
            return
        if not self.spotify.connected:
            self.on_log(f"{name}: Spotify is not connected.")
            return
        def work() -> None:
            try:
                self.spotify.play_playlist(playlist)
                self.on_log(f"▶ {name} — playlist started ({source}).")
            except Exception as exc:
                self.on_log(f"{name}: playback failed: {exc}")
        threading.Thread(target=work, name="play", daemon=True).start()

    # ----- visuals ------------------------------------------------------
    def effective_colors(self, name: str) -> tuple:
        """(main_hex, accent_hex): manual override, else auto from the portrait."""
        hero = self.cfg.heroes.get(name) or Hero()
        auto = None
        if not (colors.is_hex(hero.color_main) and colors.is_hex(hero.color_accent)):
            auto = self._palette_cache.get(name)
            if auto is None:
                auto = colors.palette_from_image(hero.art_path("portrait"))
                self._palette_cache[name] = auto
        main = hero.color_main if colors.is_hex(hero.color_main) else auto[0]
        accent = hero.color_accent if colors.is_hex(hero.color_accent) else auto[1]
        return main, accent

    def invalidate_palette(self, name: str) -> None:
        self._palette_cache.pop(name, None)

    def visuals_for(self, name: str) -> HeroVisuals:
        hero = self.cfg.heroes.get(name) or Hero()
        main, accent = self.effective_colors(name)
        return HeroVisuals(
            name=name,
            logo=hero.art_path("logo"),
            portrait=hero.art_path("portrait"),
            signature=hero.art_path("signature"),
            background=hero.art_path("background"),
            main_hex=main, accent_hex=accent)

    def refresh_current_visuals(self) -> None:
        if self.current_hero:
            self.feed.set_hero(self.visuals_for(self.current_hero))

    # ----- now playing --------------------------------------------------
    def _poll_now_playing(self) -> None:
        last_url = ""
        while not self._np_stop.is_set():
            if self.spotify.connected:
                info = self.spotify.now_playing()
                if info:
                    self.feed.set_track(Track(
                        title=info["title"], artist=info["artist"],
                        art_url=info["art_url"],
                        progress_ms=info["progress_ms"],
                        duration_ms=info["duration_ms"],
                        playing=info["playing"], fetched_at=time.time()))
                    if info["art_url"] and info["art_url"] != last_url:
                        last_url = info["art_url"]
                        spotify_mod.download_art(info["art_url"])
            self._np_stop.wait(1.5)
