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

from .config import Config, app_data_dir
from .capture import ScreenGrabber
from .recognizer import HeroRecognizer

LogFn = Callable[[str], None]
HeroFn = Callable[[str], None]
FailFn = Callable[[], None]
EventFn = Callable[[dict], None]


class HeroSource:
    name = "base"

    @property
    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def start(self, on_hero: HeroFn, on_log: LogFn,
              on_failed: Optional[FailFn] = None,
              on_event: Optional[EventFn] = None) -> None:
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

    def start(self, on_hero: HeroFn, on_log: LogFn,
              on_failed: Optional[FailFn] = None,
              on_event: Optional[EventFn] = None) -> None:
        # Screen capture has no rich game events; on_event is ignored.
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
        self._events_path: Optional[str] = None
        self._stderr_f = None

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

    def start(self, on_hero: HeroFn, on_log: LogFn,
              on_failed: Optional[FailFn] = None,
              on_event: Optional[EventFn] = None) -> None:
        cmd = self._resolve_cmd()
        if not cmd:
            on_log("Cannot start GEP: " + self.unavailable_reason())
            if on_failed:
                on_failed()
            return
        self._stop.clear()
        env = os.environ.copy()
        data_dir = app_data_dir()
        # The bridge communicates over a file we tail, not stdout: a packaged
        # windowed Electron app on Windows has no usable stdout pipe, so stdout
        # messages (hero data and status) silently vanish.
        self._events_path = os.path.join(data_dir, "gep-events.jsonl")
        try:
            open(self._events_path, "w", encoding="utf-8").close()  # truncate
        except OSError:
            pass
        env["RIVALSRADIO_GEP_EVENTS"] = self._events_path
        if getattr(self.cfg, "gep_debug", False):
            log_path = os.path.join(data_dir, "gep-debug.log")
            env["RIVALSRADIO_GEP_DEBUG"] = "1"
            env["RIVALSRADIO_GEP_LOG"] = log_path
            on_log(f"GEP debug logging enabled → {log_path}")
        # Capture the bridge's stderr to a file for crash diagnosis.
        try:
            self._stderr_f = open(os.path.join(data_dir, "gep-stderr.log"), "w", encoding="utf-8")
        except OSError:
            self._stderr_f = subprocess.DEVNULL
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=self._stderr_f, env=env)
        except Exception as exc:
            on_log(f"Failed to launch GEP bridge: {exc}")
            if on_failed:
                on_failed()
            return
        on_log("Overwolf GEP bridge launched — waiting for hero data…")
        self._thread = threading.Thread(
            target=self._read, args=(on_hero, on_log, on_failed, on_event),
            name="gep-source", daemon=True)
        self._thread.start()

    def _read(self, on_hero: HeroFn, on_log: LogFn,
              on_failed: Optional[FailFn] = None,
              on_event: Optional[EventFn] = None) -> None:
        """Tail the bridge's events file (newline-delimited JSON)."""
        last: Optional[str] = None
        try:
            fh = open(self._events_path, "r", encoding="utf-8")
        except OSError:
            fh = None
        try:
            while not self._stop.is_set():
                line = fh.readline() if fh else ""
                if not line:
                    if self._proc and self._proc.poll() is not None:
                        break  # bridge exited
                    self._stop.wait(0.2)
                    continue
                line = line.strip()
                if not line or line[0] == "#":
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")
                if mtype == "hero":
                    hero = msg.get("hero")
                    if hero and hero != last:
                        last = hero
                        on_hero(hero)
                elif mtype == "log":
                    on_log(f"[bridge] {msg.get('message', '')}")
                elif mtype in ("match", "stats", "needs_admin") and on_event:
                    on_event(msg)
        finally:
            if fh:
                fh.close()
        if not self._stop.is_set():
            on_log("GEP bridge exited.")
            if on_failed:
                on_failed()

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
        if self._stderr_f not in (None, subprocess.DEVNULL):
            try:
                self._stderr_f.close()
            except Exception:
                pass
        self._stderr_f = None
        self._thread = None


def make_hero_source(cfg: Config) -> HeroSource:
    if cfg.hero_source == "gep":
        return GepHeroSource(cfg)
    return ScreenHeroSource(cfg)
