"""Spotify playback control (spotipy) + album-art cache.

Premium is required for programmatic playback. Playlists start shuffled on a
random track. Album art is cached to disk with an atomic write so renderers
can never read a half-downloaded image.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import threading
import urllib.request
from typing import Optional

from . import paths
from .config import SpotifySettings

SCOPE = "user-modify-playback-state user-read-playback-state"


def to_playlist_uri(value: str) -> str:
    """Accept a spotify:playlist:… URI or an open.spotify.com URL."""
    value = (value or "").strip()
    m = re.search(r"open\.spotify\.com/playlist/([A-Za-z0-9]+)", value)
    if m:
        return f"spotify:playlist:{m.group(1)}"
    return value


class Spotify:
    def __init__(self, settings: SpotifySettings) -> None:
        self.settings = settings
        self._sp = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._sp is not None

    def connect(self) -> None:
        """Authenticate (opens a browser on the very first run) and verify."""
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        if not (self.settings.client_id and self.settings.client_secret):
            raise ValueError("Spotify Client ID and Secret are required.")
        auth = SpotifyOAuth(
            client_id=self.settings.client_id,
            client_secret=self.settings.client_secret,
            redirect_uri=self.settings.redirect_uri,
            scope=SCOPE,
            cache_path=os.path.join(paths.data_dir(), ".spotify_token"),
            open_browser=True,
        )
        sp = spotipy.Spotify(auth_manager=auth)
        sp.me()                       # force token fetch so failures surface now
        with self._lock:
            self._sp = sp

    # ----- playback -----------------------------------------------------
    def _device_id(self) -> Optional[str]:
        data = self._sp.devices() or {}
        devices = data.get("devices", [])
        if not devices:
            return None
        if self.settings.device_name:
            for d in devices:
                if d["name"] == self.settings.device_name:
                    return d["id"]
        for d in devices:
            if d.get("is_active"):
                return d["id"]
        return devices[0]["id"]

    def play_playlist(self, playlist: str) -> None:
        """Start the playlist shuffled, on a random track."""
        if not self._sp:
            raise RuntimeError("Spotify is not connected.")
        uri = to_playlist_uri(playlist)
        if not uri:
            raise ValueError("No playlist configured.")
        device = self._device_id()
        if device is None:
            raise RuntimeError("No Spotify device found. Open Spotify and "
                               "play anything once, then retry.")
        try:
            self._sp.shuffle(True, device_id=device)
        except Exception:
            pass                       # non-fatal; still start playback
        offset = None
        try:
            total = int((self._sp.playlist(uri, fields="tracks.total") or {})
                        .get("tracks", {}).get("total", 0) or 0)
            if total > 1:
                offset = {"position": random.randint(0, total - 1)}
        except Exception:
            pass
        self._sp.start_playback(device_id=device, context_uri=uri, offset=offset)

    def now_playing(self) -> Optional[dict]:
        """{title, artist, art_url, progress_ms, duration_ms, playing} or None."""
        if not self._sp:
            return None
        try:
            data = self._sp.current_playback()
        except Exception:
            return None
        if not data or not data.get("item"):
            return None
        item = data["item"]
        images = (item.get("album", {}) or {}).get("images", []) or []
        return {
            "title": item.get("name", ""),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "art_url": images[0]["url"] if images else "",
            "progress_ms": data.get("progress_ms", 0) or 0,
            "duration_ms": item.get("duration_ms", 0) or 0,
            "playing": bool(data.get("is_playing", False)),
        }


# ----- album-art disk cache -------------------------------------------------
def cached_art(url: str) -> Optional[str]:
    """Cached file path for an art URL, or None if not downloaded yet.
    Never touches the network — safe for render loops."""
    if not url:
        return None
    dest = os.path.join(paths.art_cache_dir(),
                        hashlib.sha1(url.encode()).hexdigest() + ".img")
    return dest if os.path.exists(dest) else None


def download_art(url: str) -> Optional[str]:
    """Download art to the cache (atomic write) and return its path."""
    if not url:
        return None
    dest = os.path.join(paths.art_cache_dir(),
                        hashlib.sha1(url.encode()).hexdigest() + ".img")
    if os.path.exists(dest):
        return dest
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = resp.read()
        tmp = f"{dest}.tmp{os.getpid()}"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)
        return dest
    except Exception:
        return None
