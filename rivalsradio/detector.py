"""Hero detection receiver — the native Overwolf app's localhost endpoint.

The companion app (overwolf-app/) reads Marvel Rivals game events inside
Overwolf and POSTs JSON lines here:

    {"type": "hero", "hero": "PSYLOCKE"}
    {"type": "game", "running": true}
    {"type": "log",  "message": "..."}

Placeholder readings the game emits in lobbies / between rounds ("UNKNOWN",
"NONE", …) are dropped here so they can never reset the playlist.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
from typing import Callable, Optional

PLACEHOLDERS = {"", "unknown", "none", "null"}


class Detector:
    def __init__(self, port: int,
                 on_hero: Callable[[str], None],
                 on_game: Callable[[bool], None],
                 on_log: Callable[[str], None],
                 on_ult: Optional[Callable[[int], None]] = None) -> None:
        self.port = int(port)
        self.on_hero = on_hero
        self.on_game = on_game
        self.on_log = on_log
        self.on_ult = on_ult
        self._httpd: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.last_event_at = 0.0        # for the "Overwolf app linked" pill

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> bool:
        if self._httpd is not None:
            return True
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):      # silence stderr spam
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "POST,OPTIONS")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_POST(self):
                try:
                    n = int(self.headers.get("Content-Length", 0) or 0)
                    msg = json.loads(self.rfile.read(n).decode("utf-8"))
                except Exception:
                    msg = None
                self.send_response(200)
                self._cors()
                self.end_headers()
                self.wfile.write(b"ok")
                if not isinstance(msg, dict):
                    return
                outer.last_event_at = time.time()
                mtype = msg.get("type")
                if mtype == "hero":
                    hero = str(msg.get("hero") or "").strip()
                    if hero.lower() not in PLACEHOLDERS:
                        outer.on_hero(hero)
                elif mtype == "game":
                    outer.on_game(bool(msg.get("running")))
                elif mtype == "ult" and outer.on_ult is not None:
                    try:
                        outer.on_ult(max(0, min(100, int(msg.get("charge", 0)))))
                    except (TypeError, ValueError):
                        pass
                elif mtype == "log":
                    outer.on_log(f"[overwolf] {msg.get('message', '')}")

        try:
            self._httpd = http.server.ThreadingHTTPServer(
                ("127.0.0.1", self.port), Handler)
        except OSError as exc:
            self.on_log(f"Detector: can't listen on 127.0.0.1:{self.port} ({exc})")
            return False
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="detector", daemon=True)
        self._thread.start()
        self.on_log(f"Listening for the Overwolf app on http://127.0.0.1:{self.port}")
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        self._thread = None
