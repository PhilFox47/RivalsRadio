"""Persistent configuration for RivalsRadio.

Stored as a single JSON file next to the app data, plus a ``references/``
directory holding one PNG per calibrated hero.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional


# Default roster of Marvel Rivals heroes. This is just a starting list — the UI
# lets you add or remove heroes, so it does not need to be perfectly complete.
DEFAULT_HEROES = [
    # Vanguards
    "Captain America", "Doctor Strange", "Emma Frost", "Groot", "Hulk",
    "Magneto", "Peni Parker", "The Thing", "Thor", "Venom",
    # Duelists
    "Black Panther", "Black Widow", "Hawkeye", "Hela", "Human Torch",
    "Iron Fist", "Iron Man", "Magik", "Mister Fantastic", "Moon Knight",
    "Namor", "Phoenix", "Psylocke", "Scarlet Witch", "Spider-Man",
    "Squirrel Girl", "Star-Lord", "Storm", "The Punisher", "Winter Soldier",
    "Wolverine",
    # Strategists
    "Adam Warlock", "Cloak & Dagger", "Invisible Woman", "Jeff the Land Shark",
    "Loki", "Luna Snow", "Mantis", "Rocket Raccoon", "Ultron",
]


def app_data_dir() -> str:
    """Return (and create) a per-user directory to store config + references."""
    base = os.environ.get("RIVALSRADIO_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".rivalsradio")
    os.makedirs(base, exist_ok=True)
    os.makedirs(os.path.join(base, "references"), exist_ok=True)
    os.makedirs(os.path.join(base, "avatars"), exist_ok=True)
    return base


@dataclass
class CaptureRegion:
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0

    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0


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
    # Filename (relative to references/) of the calibration snapshot, if any.
    reference: str = ""
    # Filename (relative to avatars/) of the hero artwork shown on the Stage.
    avatar: str = ""
    # Optional manual accent override as "#RRGGBB". Blank = auto-extract from
    # the avatar image.
    accent: str = ""


@dataclass
class Config:
    capture_region: CaptureRegion = field(default_factory=CaptureRegion)
    spotify: SpotifyConfig = field(default_factory=SpotifyConfig)
    heroes: Dict[str, HeroConfig] = field(default_factory=dict)

    # Matching / detection tuning.
    match_threshold: float = 0.70   # 0..1, higher = stricter match
    poll_interval: float = 2.0      # seconds between screen reads
    confirm_count: int = 2          # consecutive matches before switching

    # Stage / presentation.
    stage_style: str = "bars"       # visualizer style: bars | mirror | radial
    show_now_playing: bool = True   # show track + album art + progress on Stage
    web_overlay_enabled: bool = False  # serve the Stage as an OBS browser source
    web_overlay_port: int = 8770

    # Onboarding / window detection.
    setup_complete: bool = False
    game_window_title: str = "Marvel Rivals"

    # Hero detection source: "auto" (prefer Overwolf GEP, fall back to screen),
    # "gep" (Overwolf bridge only), or "screen" (screen capture only).
    hero_source: str = "auto"
    # Command to launch the Overwolf GEP bridge (e.g. path to its .exe, or
    # "npm start --prefix bridge"). Blank = look for a bundled bridge.
    gep_bridge_cmd: str = ""
    # When true, the bridge writes every raw GEP event to gep-debug.log so the
    # exact field names can be confirmed/mapped from a live game.
    gep_debug: bool = False
    # Override the ow-electron packages endpoint. Blank = Overwolf PROD (default,
    # where Marvel Rivals is supported). Set the QA URL only for DEV-stage games.
    gep_packages_url: str = ""
    # Localhost port the native Overwolf app POSTs hero data to ("native" source).
    native_port: int = 8771

    @property
    def references_dir(self) -> str:
        return os.path.join(app_data_dir(), "references")

    @property
    def avatars_dir(self) -> str:
        return os.path.join(app_data_dir(), "avatars")

    def canonical_hero(self, name: str) -> str:
        """Resolve a detected hero name to the roster's canonical spelling.

        GEP reports e.g. "JEFF THE LAND SHARK"; the roster key is
        "Jeff the Land Shark". Match case-insensitively; fall back to the input.
        """
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

    # ----- persistence ---------------------------------------------------
    @classmethod
    def path(cls) -> str:
        return os.path.join(app_data_dir(), "config.json")

    @classmethod
    def load(cls) -> "Config":
        path = cls.path()
        if not os.path.exists(path):
            cfg = cls()
            cfg.ensure_default_heroes()
            cfg.save()
            return cfg
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        cfg = cls(
            capture_region=CaptureRegion(**raw.get("capture_region", {})),
            spotify=SpotifyConfig(**raw.get("spotify", {})),
            heroes={
                name: HeroConfig(**data)
                for name, data in raw.get("heroes", {}).items()
            },
            match_threshold=raw.get("match_threshold", 0.70),
            poll_interval=raw.get("poll_interval", 2.0),
            confirm_count=raw.get("confirm_count", 2),
            stage_style=raw.get("stage_style", "bars"),
            show_now_playing=raw.get("show_now_playing", True),
            web_overlay_enabled=raw.get("web_overlay_enabled", False),
            web_overlay_port=raw.get("web_overlay_port", 8770),
            setup_complete=raw.get("setup_complete", False),
            game_window_title=raw.get("game_window_title", "Marvel Rivals"),
            hero_source=raw.get("hero_source", "auto"),
            gep_bridge_cmd=raw.get("gep_bridge_cmd", ""),
            gep_debug=raw.get("gep_debug", False),
            gep_packages_url=raw.get("gep_packages_url", ""),
            native_port=raw.get("native_port", 8771),
        )
        cfg.ensure_default_heroes()
        # Spotify dropped support for "localhost" redirect URIs; migrate the old
        # default to the loopback IP so existing setups keep working.
        if cfg.spotify.redirect_uri.strip() in (
                "http://localhost:8888/callback", "http://localhost:8888/callback/"):
            cfg.spotify.redirect_uri = "http://127.0.0.1:8888/callback"
            cfg.save()
        # The QA endpoint was a dead end; default back to PROD for older configs.
        if cfg.gep_packages_url.strip() == "https://electronapi-qa.overwolf.com/packages":
            cfg.gep_packages_url = ""
            cfg.save()
        return cfg

    def save(self) -> None:
        with open(self.path(), "w", encoding="utf-8") as fh:
            json.dump(self._to_dict(), fh, indent=2)

    def _to_dict(self) -> dict:
        return {
            "capture_region": asdict(self.capture_region),
            "spotify": asdict(self.spotify),
            "heroes": {name: asdict(h) for name, h in self.heroes.items()},
            "match_threshold": self.match_threshold,
            "poll_interval": self.poll_interval,
            "confirm_count": self.confirm_count,
            "stage_style": self.stage_style,
            "show_now_playing": self.show_now_playing,
            "web_overlay_enabled": self.web_overlay_enabled,
            "web_overlay_port": self.web_overlay_port,
            "setup_complete": self.setup_complete,
            "game_window_title": self.game_window_title,
            "hero_source": self.hero_source,
            "gep_bridge_cmd": self.gep_bridge_cmd,
            "gep_debug": self.gep_debug,
            "gep_packages_url": self.gep_packages_url,
            "native_port": self.native_port,
        }

    # ----- helpers -------------------------------------------------------
    def ensure_default_heroes(self) -> None:
        for name in DEFAULT_HEROES:
            self.heroes.setdefault(name, HeroConfig())

    def reference_path(self, hero: str) -> Optional[str]:
        h = self.heroes.get(hero)
        if not h or not h.reference:
            return None
        return os.path.join(self.references_dir, h.reference)

    def heroes_with_references(self) -> Dict[str, str]:
        """Map of hero name -> absolute reference path for calibrated heroes."""
        out = {}
        for name, h in self.heroes.items():
            if h.reference:
                out[name] = os.path.join(self.references_dir, h.reference)
        return out
