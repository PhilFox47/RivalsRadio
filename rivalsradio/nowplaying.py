"""Background poller that keeps StageState's track info fresh from Spotify.

Runs on its own thread (network calls must not block the UI), and caches album
art to disk so both the Tk Stage and the web overlay can display it.
"""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
from typing import Callable, Optional

from .config import app_data_dir
from .spotify_controller import SpotifyController
from .stage_state import StageState, TrackInfo

LogFn = Callable[[str], None]


def art_cache_dir() -> str:
    path = os.path.join(app_data_dir(), "artcache")
    os.makedirs(path, exist_ok=True)
    return path


def art_path_for(url: str) -> Optional[str]:
    """Local cached path for an album-art URL, downloading it once if needed."""
    if not url:
        return None
    name = hashlib.sha1(url.encode("utf-8")).hexdigest() + ".img"
    dest = os.path.join(art_cache_dir(), name)
    if os.path.exists(dest):
        return dest
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = resp.read()
        with open(dest, "wb") as fh:
            fh.write(data)
        return dest
    except Exception:
        return None


class NowPlaying:
    def __init__(self, spotify: SpotifyController, state: StageState,
                 interval: float = 1.5, on_log: Optional[LogFn] = None) -> None:
        self.spotify = spotify
        self.state = state
        self.interval = interval
        self.on_log = on_log
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_url = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="nowplaying", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.spotify.connected:
                info = self.spotify.current_track()
                if info:
                    self.state.set_track(TrackInfo(
                        title=info["title"],
                        artist=info["artist"],
                        album_art_url=info["album_art_url"],
                        progress_ms=info["progress_ms"],
                        duration_ms=info["duration_ms"],
                        is_playing=info["is_playing"],
                    ))
                    # Warm the art cache so the UI can read it immediately.
                    if info["album_art_url"] and info["album_art_url"] != self._last_url:
                        self._last_url = info["album_art_url"]
                        art_path_for(info["album_art_url"])
            self._stop.wait(self.interval)
