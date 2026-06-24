"""Pluggable hero-detection sources.

A ``HeroSource`` reports the locally-played hero to a callback. Two backends:

- ``ScreenHeroSource``: captures the HUD region and matches it against your
  calibrated references (the default; no extra setup or dependencies).
- ``GepHeroSource``: launches the Overwolf ow-electron bridge as a child
  process and reads exact hero changes from its stdout (no screen capture).

The rest of the app (Spotify switching, Stage) is identical regardless of which
source is active.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from typing import Callable, List, Optional

from .config import Config
from .capture import ScreenGrabber
from .recognizer import HeroRecognizer

LogFn = Callable[[str], None]
HeroFn = Callable[[str], None]


class HeroSource:
    name = "base"

    @property
    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def start(self, on_hero: HeroFn, on_log: LogFn) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
class ScreenHeroSource(HeroSource):
    name = "screen"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def available(self) -> bool:
        return (self.cfg.capture_region.is_valid()
                and bool(self.cfg.heroes_with_references()))

    def unavailable_reason(self) -> str:
        if not self.cfg.capture_region.is_valid():
            return "capture region is not set (see Settings)."
        if not self.cfg.heroes_with_references():
            return "no heroes have calibrated references yet."
        return ""

    def start(self, on_hero: HeroFn, on_log: LogFn) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(on_hero, on_log), name="screen-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self, on_hero: HeroFn, on_log: LogFn) -> None:
        recognizer = HeroRecognizer()
        recognizer.load_references(self.cfg.heroes_with_references())
        on_log(f"Screen detection started — watching {recognizer.hero_count} "
               f"calibrated hero(es).")
        try:
            grabber = ScreenGrabber()
        except Exception as exc:  # pragma: no cover - display dependent
            on_log(f"Failed to initialise screen capture: {exc}")
            return

        pending: Optional[str] = None
        pending_count = 0
        emitted: Optional[str] = None
        try:
            while not self._stop.is_set():
                frame = grabber.grab(self.cfg.capture_region)
                if frame is not None:
                    name, score = recognizer.best_match(frame)
                    if name is not None and score >= self.cfg.match_threshold:
                        if name == pending:
                            pending_count += 1
                        else:
                            pending, pending_count = name, 1
                        if pending_count >= self.cfg.confirm_count and name != emitted:
                            emitted = name
                            on_hero(name)
                    # Below-threshold reads (menus/loading) are ignored.
                self._stop.wait(self.cfg.poll_interval)
        finally:
            grabber.close()
            on_log("Screen detection stopped.")


# ---------------------------------------------------------------------------
class GepHeroSource(HeroSource):
    name = "gep"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def _resolve_cmd(self) -> Optional[List[str]]:
        """Resolve the command that launches the Overwolf bridge."""
        if self.cfg.gep_bridge_cmd.strip():
            return shlex.split(self.cfg.gep_bridge_cmd, posix=(os.name != "nt"))
        # Look for a bundled bridge next to the app / project.
        roots = [os.path.dirname(os.path.abspath(sys.argv[0])), os.getcwd()]
        for root in roots:
            exe = os.path.join(root, "bridge", "RivalsRadioBridge.exe")
            if os.path.exists(exe):
                return [exe]
            pkg = os.path.join(root, "bridge", "package.json")
            if os.path.exists(pkg):
                return ["npm", "start", "--prefix", os.path.join(root, "bridge")]
        return None

    @property
    def available(self) -> bool:
        return self._resolve_cmd() is not None

    def unavailable_reason(self) -> str:
        return ("Overwolf GEP bridge not found. Build it (see bridge/README) and "
                "set its command in Settings, or keep the bridge/ folder next to "
                "the app.")

    def start(self, on_hero: HeroFn, on_log: LogFn) -> None:
        cmd = self._resolve_cmd()
        if not cmd:
            on_log("Cannot start GEP: " + self.unavailable_reason())
            return
        self._stop.clear()
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1)
        except Exception as exc:
            on_log(f"Failed to launch GEP bridge: {exc}")
            return
        on_log("Overwolf GEP bridge launched — waiting for hero data…")
        self._thread = threading.Thread(
            target=self._read, args=(on_hero, on_log), name="gep-source", daemon=True)
        self._thread.start()

    def _read(self, on_hero: HeroFn, on_log: LogFn) -> None:
        assert self._proc and self._proc.stdout
        last: Optional[str] = None
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore non-JSON log noise from the bridge
            if msg.get("type") == "hero":
                hero = msg.get("hero")
                if hero and hero != last:
                    last = hero
                    on_hero(hero)
            elif msg.get("type") == "log":
                on_log(f"[bridge] {msg.get('message', '')}")
        if not self._stop.is_set():
            on_log("GEP bridge exited.")

    def stop(self) -> None:
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        self._thread = None


def make_hero_source(cfg: Config) -> HeroSource:
    if cfg.hero_source == "gep":
        return GepHeroSource(cfg)
    return ScreenHeroSource(cfg)
