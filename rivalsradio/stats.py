"""Per-session play statistics for RivalsRadio.

Tracks what we can reliably derive from hero changes (per-hero playtime, how
often each hero was played, the playlist that was on) plus best-effort match
results and KDA forwarded from the Overwolf GEP bridge. Everything is in-memory
for the current session and can be reset.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class HeroStat:
    seconds: float = 0.0
    plays: int = 0
    last_playlist: str = ""


class SessionStats:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        now = time.time()
        self.session_start = now
        self.heroes: Dict[str, HeroStat] = {}
        self._current: Optional[str] = None
        self._since: float = now
        self.matches = 0
        self.wins = 0
        self.losses = 0
        # Session KDA totals (summed at the end of each match).
        self.kills = 0
        self.deaths = 0
        self.assists = 0
        # Current match's running KDA (the latest snapshot from the bridge).
        self._mk = 0
        self._md = 0
        self._ma = 0

    # ----- playtime (reliable, derived from hero changes) --------------
    def _accrue(self) -> None:
        now = time.time()
        if self._current and self._current in self.heroes:
            self.heroes[self._current].seconds += now - self._since
        self._since = now

    def note_hero(self, hero: str, playlist: str = "") -> None:
        self._accrue()
        stat = self.heroes.setdefault(hero, HeroStat())
        if hero != self._current:
            stat.plays += 1
        if playlist:
            stat.last_playlist = playlist
        self._current = hero

    # ----- match results / KDA (best-effort, from GEP) -----------------
    def note_kda(self, kills: int, deaths: int, assists: int) -> None:
        self._mk, self._md, self._ma = int(kills), int(deaths), int(assists)

    def note_match_result(self, result: str) -> None:
        self.matches += 1
        if result == "victory":
            self.wins += 1
        elif result == "defeat":
            self.losses += 1
        # Fold the finished match's KDA into the session totals.
        self.kills += self._mk
        self.deaths += self._md
        self.assists += self._ma
        self._mk = self._md = self._ma = 0

    # ----- read-out ----------------------------------------------------
    def snapshot(self) -> dict:
        self._accrue()
        k = self.kills + self._mk
        d = self.deaths + self._md
        a = self.assists + self._ma
        decided = self.wins + self.losses
        heroes: List[dict] = [
            {"hero": name, "seconds": s.seconds, "plays": s.plays,
             "playlist": s.last_playlist}
            for name, s in self.heroes.items()
        ]
        heroes.sort(key=lambda h: h["seconds"], reverse=True)
        return {
            "duration": time.time() - self.session_start,
            "matches": self.matches,
            "wins": self.wins,
            "losses": self.losses,
            "winrate": (self.wins / decided * 100.0) if decided else None,
            "kills": k, "deaths": d, "assists": a,
            "kda": ((k + a) / d) if d else float(k + a),
            "current": self._current,
            "heroes": heroes,
        }
