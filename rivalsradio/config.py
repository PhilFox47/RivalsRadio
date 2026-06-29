"""Persistent configuration for RivalsRadio.

Stored as a single JSON file next to the app data, plus image folders
(``avatars/``, ``logos/``, ``signatures/``) for per-hero Stage artwork.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, asdict
from typing import Dict, Optional


def app_data_dir() -> str:
    """Return (and create) a per-user directory to store config + artwork."""
    base = os.environ.get("RIVALSRADIO_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".rivalsradio")
    os.makedirs(base, exist_ok=True)
    os.makedirs(os.path.join(base, "avatars"), exist_ok=True)
    return base


@dataclass
class SpotifyConfig:
    client_id: str = ""
    client_secret: str = ""
    # Spotify requires the loopback IP (127.0.0.1), not "localhost", in redirect
    # URIs — and the value here must match the dashboard entry exactly.
    redirect_uri: str = "http://127.0.0.1:8888/callback"
    # Optional: name of the device to start playback on (e.g. "DESKTOP-PC").
    # Leave blank to use whatever device is currently active in Spotify.
    device_name: str = ""


@dataclass
class HeroConfig:
    playlist_uri: str = ""
    # Filename (relative to avatars/) of legacy hero artwork (unused by the Stage
    # now; kept for backward-compat with older configs).
    avatar: str = ""
    # Filename (relative to logos/) of the hero logo shown centred on the Stage
    # (pulses with the audio).
    logo: str = ""
    # Filename (relative to signatures/) of the hero signature shown top-right.
    signature: str = ""
    # Filename (relative to portraits/) of the hero portrait. Not shown on the
    # Stage (yet) — used as the source for automatic colour extraction.
    portrait: str = ""
    # Per-hero Stage colours as "#RRGGBB". main = background/glow, accent = bars.
    # Blank = auto-extract from the portrait/logo, falling back to the default.
    color_main: str = ""
    color_accent: str = ""
    # Legacy single accent (migrated into color_accent on load).
    accent: str = ""

    def is_empty(self) -> bool:
        """True if the hero has no user-set data (a bare auto-added entry)."""
        return not any((self.playlist_uri, self.avatar, self.logo, self.signature,
                        self.portrait, self.color_main, self.color_accent, self.accent))

    @classmethod
    def from_dict(cls, data: dict) -> "HeroConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Config:
    spotify: SpotifyConfig = field(default_factory=SpotifyConfig)
    heroes: Dict[str, HeroConfig] = field(default_factory=dict)

    # Stage / presentation.
    stage_style: str = "bars"       # visualizer style: bars
    stage_fps: int = 144            # Stage redraw target (frames per second)
    show_now_playing: bool = True   # show track + album art + progress on Stage
    web_overlay_enabled: bool = False  # serve the Stage as an OBS browser source
    web_overlay_port: int = 8770

    setup_complete: bool = False

    # Hero detection source: "native" (Overwolf native app over localhost) or
    # "gep" (ow-electron bridge).
    hero_source: str = "native"
    # Command to launch the ow-electron GEP bridge. Blank = bundled bridge.
    gep_bridge_cmd: str = ""
    # When true, the bridge writes raw GEP events to gep-debug.log.
    gep_debug: bool = False
    # Override the ow-electron packages endpoint. Blank = Overwolf PROD.
    gep_packages_url: str = ""
    # Localhost port the native Overwolf app POSTs hero data to ("native" source).
    native_port: int = 8771
    # One-time flag: the legacy pre-filled default roster has been purged so the
    # list now auto-populates purely from detected heroes.
    roster_purged: bool = False

    @property
    def avatars_dir(self) -> str:
        return os.path.join(app_data_dir(), "avatars")

    @property
    def logos_dir(self) -> str:
        d = os.path.join(app_data_dir(), "logos")
        os.makedirs(d, exist_ok=True)
        return d

    @property
    def signatures_dir(self) -> str:
        d = os.path.join(app_data_dir(), "signatures")
        os.makedirs(d, exist_ok=True)
        return d

    @property
    def portraits_dir(self) -> str:
        d = os.path.join(app_data_dir(), "portraits")
        os.makedirs(d, exist_ok=True)
        return d

    def canonical_hero(self, name: str) -> str:
        """Resolve a detected hero name to the roster's canonical spelling."""
        if name in self.heroes:
            return name
        low = name.lower()
        for key in self.heroes:
            if key.lower() == low:
                return key
        return name

    def avatar_path(self, hero: str) -> Optional[str]:
        h = self.heroes.get(hero)
        if not h or not h.avatar:
            return None
        return os.path.join(self.avatars_dir, h.avatar)

    def logo_path(self, hero: str) -> Optional[str]:
        h = self.heroes.get(hero)
        if not h or not h.logo:
            return None
        return os.path.join(self.logos_dir, h.logo)

    def signature_path(self, hero: str) -> Optional[str]:
        h = self.heroes.get(hero)
        if not h or not h.signature:
            return None
        return os.path.join(self.signatures_dir, h.signature)

    def portrait_path(self, hero: str) -> Optional[str]:
        h = self.heroes.get(hero)
        if not h or not h.portrait:
            return None
        return os.path.join(self.portraits_dir, h.portrait)

    # ----- persistence ---------------------------------------------------
    @classmethod
    def path(cls) -> str:
        return os.path.join(app_data_dir(), "config.json")

    @classmethod
    def load(cls) -> "Config":
        path = cls.path()
        if not os.path.exists(path):
            # Start with an empty roster — it auto-populates from detected heroes.
            cfg = cls(roster_purged=True)
            cfg.save()
            return cfg
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        cfg = cls(
            spotify=SpotifyConfig(**{
                k: v for k, v in raw.get("spotify", {}).items()
                if k in {f.name for f in fields(SpotifyConfig)}}),
            heroes={name: HeroConfig.from_dict(data)
                    for name, data in raw.get("heroes", {}).items()},
            stage_style=raw.get("stage_style", "bars"),
            stage_fps=int(raw.get("stage_fps", 144)),
            show_now_playing=raw.get("show_now_playing", True),
            web_overlay_enabled=raw.get("web_overlay_enabled", False),
            web_overlay_port=raw.get("web_overlay_port", 8770),
            setup_complete=raw.get("setup_complete", False),
            hero_source=raw.get("hero_source", "native"),
            gep_bridge_cmd=raw.get("gep_bridge_cmd", ""),
            gep_debug=raw.get("gep_debug", False),
            gep_packages_url=raw.get("gep_packages_url", ""),
            native_port=raw.get("native_port", 8771),
            roster_purged=raw.get("roster_purged", False),
        )
        cfg._migrate()
        return cfg

    def _migrate(self) -> None:
        changed = False
        if self.spotify.redirect_uri.strip() in (
                "http://localhost:8888/callback", "http://localhost:8888/callback/"):
            self.spotify.redirect_uri = "http://127.0.0.1:8888/callback"
            changed = True
        if self.gep_packages_url.strip() == "https://electronapi-qa.overwolf.com/packages":
            self.gep_packages_url = ""
            changed = True
        # Screen capture was removed; point old sources at native.
        if self.hero_source in ("auto", "screen"):
            self.hero_source = "native"
            changed = True
        # Fold the legacy single accent into the new accent colour.
        for h in self.heroes.values():
            if h.accent and not h.color_accent:
                h.color_accent = h.accent
                changed = True
        # One-time purge of the legacy pre-filled roster: drop bare, unconfigured
        # entries so the list now auto-populates purely from detected heroes.
        if not self.roster_purged:
            self.heroes = {name: h for name, h in self.heroes.items()
                           if not h.is_empty()}
            self.roster_purged = True
            changed = True
        if changed:
            self.save()

    def save(self) -> None:
        with open(self.path(), "w", encoding="utf-8") as fh:
            json.dump(self._to_dict(), fh, indent=2)

    def _to_dict(self) -> dict:
        return {
            "spotify": asdict(self.spotify),
            "heroes": {name: asdict(h) for name, h in self.heroes.items()},
            "stage_style": self.stage_style,
            "stage_fps": self.stage_fps,
            "show_now_playing": self.show_now_playing,
            "web_overlay_enabled": self.web_overlay_enabled,
            "web_overlay_port": self.web_overlay_port,
            "setup_complete": self.setup_complete,
            "hero_source": self.hero_source,
            "gep_bridge_cmd": self.gep_bridge_cmd,
            "gep_debug": self.gep_debug,
            "gep_packages_url": self.gep_packages_url,
            "native_port": self.native_port,
            "roster_purged": self.roster_purged,
        }
