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
    redirect_uri: str = "http://localhost:8888/callback"
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

    @property
    def references_dir(self) -> str:
        return os.path.join(app_data_dir(), "references")

    @property
    def avatars_dir(self) -> str:
        return os.path.join(app_data_dir(), "avatars")

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
        )
        cfg.ensure_default_heroes()
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
