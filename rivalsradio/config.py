"""Configuration: settings + hero roster, persisted as one JSON file.

First run seeds the roster (heroes, playlists, colours) and the hero art from
the assets shipped with the app, so a fresh install is immediately usable.
If a pre-0.6 config exists, the user's Spotify credentials and per-hero
customisations are imported once.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field, fields, asdict
from typing import Dict, Optional

from . import paths
from .paths import ART_KINDS

CONFIG_NAME = "rivalsradio.json"
LEGACY_CONFIG = "config.json"


@dataclass
class SpotifySettings:
    client_id: str = ""
    client_secret: str = ""
    # Spotify requires the loopback IP (127.0.0.1) — it rejects "localhost".
    redirect_uri: str = "http://127.0.0.1:8888/callback"
    device_name: str = ""


@dataclass
class StageSettings:
    fps: int = 144                  # render target (30–160)
    bg_blur: int = 12               # background picture blur (px)
    bg_dim: int = 55                # background picture darkening (0–100)
    particles: bool = True          # drifting accent particles
    glow: int = 70                  # logo glow strength (0–100)
    pulse: int = 90                 # logo beat-pulse depth (0–100)
    show_now_playing: bool = True
    switch_anim: bool = True        # hero-switch panel animation


@dataclass
class Hero:
    playlist: str = ""              # Spotify playlist URL or URI
    logo: str = ""                  # filenames inside the user art dirs
    portrait: str = ""
    signature: str = ""
    background: str = ""
    color_main: str = ""            # blank = auto from portrait
    color_accent: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Hero":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def art_file(self, kind: str) -> str:
        return getattr(self, kind, "")

    def art_path(self, kind: str) -> Optional[str]:
        name = self.art_file(kind)
        if not name:
            return None
        p = os.path.join(paths.art_dir(kind), name)
        return p if os.path.exists(p) else None

    def art_complete(self) -> bool:
        return all(self.art_file(k) for k in ART_KINDS)


@dataclass
class Config:
    spotify: SpotifySettings = field(default_factory=SpotifySettings)
    stage: StageSettings = field(default_factory=StageSettings)
    heroes: Dict[str, Hero] = field(default_factory=dict)
    detector_port: int = 8771       # the native Overwolf app POSTs here
    autostart: bool = True          # connect + listen on launch

    # ----- roster ---------------------------------------------------------
    def canonical(self, name: str) -> str:
        """Resolve a detected name to the roster's spelling (case-insensitive)."""
        if name in self.heroes:
            return name
        low = name.lower()
        for key in self.heroes:
            if key.lower() == low:
                return key
        return name

    # ----- persistence ------------------------------------------------
    @staticmethod
    def path() -> str:
        return os.path.join(paths.data_dir(), CONFIG_NAME)

    def save(self) -> None:
        doc = {
            "version": 1,
            "spotify": asdict(self.spotify),
            "stage": asdict(self.stage),
            "detector_port": self.detector_port,
            "autostart": self.autostart,
            "heroes": {n: asdict(h) for n, h in self.heroes.items()},
        }
        tmp = self.path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, self.path())

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        p = cls.path()
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
            except Exception:
                raw = {}
            sp = raw.get("spotify", {})
            cfg.spotify = SpotifySettings(**{
                k: v for k, v in sp.items()
                if k in {f.name for f in fields(SpotifySettings)}})
            st = raw.get("stage", {})
            cfg.stage = StageSettings(**{
                k: v for k, v in st.items()
                if k in {f.name for f in fields(StageSettings)}})
            cfg.detector_port = int(raw.get("detector_port", 8771))
            cfg.autostart = bool(raw.get("autostart", True))
            cfg.heroes = {n: Hero.from_dict(h)
                          for n, h in raw.get("heroes", {}).items()}
            return cfg
        # First run: seed from the shipped defaults (and any pre-0.6 config).
        cfg._seed_from_bundle()
        cfg._import_legacy()
        cfg.save()
        return cfg

    # ----- first-run seeding -------------------------------------------
    def _seed_from_bundle(self) -> None:
        """Install the shipped roster + art into the user data dir."""
        try:
            with open(paths.bundled("assets", "heroes", "default_heroes.json"),
                      "r", encoding="utf-8") as fh:
                defaults = json.load(fh)
        except Exception:
            return
        for name, data in defaults.items():
            hero = Hero.from_dict(data)
            for kind in ART_KINDS:
                fname = hero.art_file(kind)
                if not fname:
                    continue
                src = paths.bundled("assets", "heroes", kind + "s", fname)
                dst = os.path.join(paths.art_dir(kind), fname)
                if os.path.exists(src) and not os.path.exists(dst):
                    try:
                        shutil.copyfile(src, dst)
                    except OSError:
                        setattr(hero, kind, "")
            self.heroes[name] = hero

    def _import_legacy(self) -> None:
        """One-time import from a pre-0.6 config: Spotify credentials plus any
        per-hero customisations (playlists, colours, backgrounds)."""
        legacy = os.path.join(paths.data_dir(), LEGACY_CONFIG)
        if not os.path.exists(legacy):
            return
        try:
            with open(legacy, "r", encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            return
        sp = old.get("spotify", {})
        if sp.get("client_id"):
            self.spotify.client_id = sp.get("client_id", "")
            self.spotify.client_secret = sp.get("client_secret", "")
            self.spotify.redirect_uri = sp.get(
                "redirect_uri", self.spotify.redirect_uri)
            self.spotify.device_name = sp.get("device_name", "")
        for name, h in old.get("heroes", {}).items():
            hero = self.heroes.setdefault(self.canonical(name), Hero())
            if h.get("playlist_uri"):
                hero.playlist = h["playlist_uri"]
            for field_name, old_key in (("color_main", "color_main"),
                                        ("color_accent", "color_accent")):
                if h.get(old_key):
                    setattr(hero, field_name, h[old_key])
            # Backgrounds lived in a different folder pre-0.6; migrate the file.
            bg = h.get("background", "")
            if bg:
                src = os.path.join(paths.data_dir(), "backgrounds", bg)
                dst = os.path.join(paths.art_dir("background"), bg)
                if os.path.exists(src) and not os.path.exists(dst):
                    try:
                        shutil.copyfile(src, dst)
                    except OSError:
                        continue
                if os.path.exists(dst):
                    hero.background = bg
