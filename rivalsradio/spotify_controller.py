"""Thin wrapper around Spotipy for starting playlist playback.

Requires a Spotify **Premium** account (the Web API only permits programmatic
playback control on Premium) and a registered app at
https://developer.spotify.com/dashboard to obtain a client id/secret.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from .config import SpotifyConfig, app_data_dir

SCOPE = "user-modify-playback-state user-read-playback-state"


class SpotifyController:
    def __init__(self, cfg: SpotifyConfig) -> None:
        self.cfg = cfg
        self._sp = None  # spotipy.Spotify, created on connect()

    def connect(self) -> None:
        """Authenticate (opens a browser on first run) and build the client."""
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        if not (self.cfg.client_id and self.cfg.client_secret):
            raise ValueError("Spotify client id and secret are required.")

        cache_path = os.path.join(app_data_dir(), ".spotify_token_cache")
        auth = SpotifyOAuth(
            client_id=self.cfg.client_id,
            client_secret=self.cfg.client_secret,
            redirect_uri=self.cfg.redirect_uri,
            scope=SCOPE,
            cache_path=cache_path,
            open_browser=True,
        )
        self._sp = spotipy.Spotify(auth_manager=auth)
        # Force a token fetch / sanity call so failures surface immediately.
        self._sp.me()

    @property
    def connected(self) -> bool:
        return self._sp is not None

    def list_devices(self) -> List[Tuple[str, str]]:
        """Return [(device_name, device_id), ...] of available Spotify devices."""
        if not self._sp:
            return []
        data = self._sp.devices() or {}
        return [(d["name"], d["id"]) for d in data.get("devices", [])]

    def _resolve_device_id(self) -> Optional[str]:
        """Pick the device to play on, honouring a configured device name."""
        devices = self.list_devices()
        if not devices:
            return None
        if self.cfg.device_name:
            for name, dev_id in devices:
                if name == self.cfg.device_name:
                    return dev_id
        # Fall back to the currently active device, else the first available.
        data = self._sp.current_playback() if self._sp else None
        if data and data.get("device"):
            return data["device"]["id"]
        return devices[0][1]

    def play_playlist(self, playlist_uri: str) -> None:
        """Start playback of ``playlist_uri`` on the chosen device."""
        if not self._sp:
            raise RuntimeError("Spotify is not connected.")
        if not playlist_uri:
            raise ValueError("No playlist URI provided.")
        device_id = self._resolve_device_id()
        if device_id is None:
            raise RuntimeError(
                "No active Spotify device found. Open Spotify on a device "
                "(start playing anything once) and try again."
            )
        self._sp.start_playback(device_id=device_id, context_uri=playlist_uri)
