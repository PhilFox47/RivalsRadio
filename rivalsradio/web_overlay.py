"""Local web server that serves the Stage as an OBS browser source.

Add a Browser Source in OBS pointing at http://localhost:<port>/ and it renders
the same hero/accent/track/visualizer as the desktop Stage, reading from the
shared StageState via a small JSON endpoint.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .stage_state import StageState
from . import nowplaying

_PAGE = """<!doctype html><html><head><meta charset='utf-8'>
<title>RivalsRadio Stage</title>
<style>html,body{margin:0;height:100%;background:transparent;overflow:hidden;
font-family:'Segoe UI',sans-serif}canvas{display:block}</style></head>
<body><canvas id='c'></canvas><script>
const cv=document.getElementById('c'),ctx=cv.getContext('2d');
let st={hero:null,accent:'#1DB954',track:{},spectrum:[]};
let levels=[],avatar=new Image(),art=new Image(),curHero=null,curArt='';
function resize(){cv.width=innerWidth;cv.height=innerHeight;}
addEventListener('resize',resize);resize();
async function poll(){try{const r=await fetch('/state');st=await r.json();
 if(st.hero!==curHero){curHero=st.hero;avatar=new Image();avatar.src='/avatar?'+Date.now();}
 const u=st.track&&st.track.album_art_url;if(u&&u!==curArt){curArt=u;art=new Image();art.src='/art?'+Date.now();}
}catch(e){}setTimeout(poll,120);}poll();
function hx(h,f){const n=parseInt(h.slice(1),16);let r=(n>>16)&255,g=(n>>8)&255,b=n&255;
 r=Math.min(255,r*f)|0;g=Math.min(255,g*f)|0;b=Math.min(255,b*f)|0;return'rgb('+r+','+g+','+b+')';}
function draw(){const w=cv.width,h=cv.height;ctx.clearRect(0,0,w,h);
 const mc=st.main||st.accent;
 const g=ctx.createLinearGradient(0,0,0,h);g.addColorStop(0,hx(mc,0.9));g.addColorStop(1,'#05060a');
 ctx.fillStyle=g;ctx.fillRect(0,0,w,h);
 const rg=ctx.createRadialGradient(w/2,h*0.52,10,w/2,h*0.52,Math.min(w,h)*0.5);
 rg.addColorStop(0,hx(mc,0.5));rg.addColorStop(1,'rgba(0,0,0,0)');ctx.fillStyle=rg;ctx.fillRect(0,0,w,h);
 if(avatar.complete&&avatar.naturalWidth){const th=h*0.82,r=th/avatar.naturalHeight;
  let tw=avatar.naturalWidth*r;ctx.drawImage(avatar,(w-tw)/2,h*0.54-th/2+h*0.04,tw,th);}
 ctx.textAlign='center';ctx.fillStyle='#fff';ctx.font='bold '+(h*0.075)+'px Segoe UI';
 ctx.fillText(st.hero||'Waiting for hero…',w/2,h*0.14);
 // now playing
 const t=st.track||{};const pad=h*0.03,a=h*0.13,y=h-pad-a;
 if(art.complete&&art.naturalWidth)ctx.drawImage(art,pad,y,a,a);
 ctx.textAlign='left';ctx.fillStyle='#fff';ctx.font='bold '+(h*0.030)+'px Segoe UI';
 ctx.fillText(t.title||'',pad+a+w*0.012,y+h*0.04);
 ctx.fillStyle='#c9c9c9';ctx.font=(h*0.022)+'px Segoe UI';
 ctx.fillText(t.artist||'',pad+a+w*0.012,y+h*0.075);
 const pbw=w*0.32,pby=y+a-h*0.012,tx=pad+a+w*0.012;
 ctx.fillStyle='#2a2e33';ctx.fillRect(tx,pby,pbw,h*0.008);
 const fr=t.duration_ms?Math.min(1,(t.progress_ms||0)/t.duration_ms):0;
 ctx.fillStyle='#fff';ctx.fillRect(tx,pby,pbw*fr,h*0.008);
 // visualizer bars
 const sp=st.spectrum||[],n=sp.length;if(levels.length!==n)levels=new Array(n).fill(0);
 const margin=w*0.04,us=w-2*margin,gap=us/n*0.25,bw=(us-gap*(n-1))/n,by=h*0.97,mx=h*0.32;
 for(let i=0;i<n;i++){let tg=Math.max(sp[i],0.03);levels[i]+=(tg-levels[i])*0.5;
  const bh=2+levels[i]*mx,x=margin+i*(bw+gap);ctx.fillStyle=levels[i]>0.6?hx(st.accent,1.4):st.accent;
  ctx.fillRect(x,by-bh,bw,bh);}
 requestAnimationFrame(draw);}
requestAnimationFrame(draw);
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence console spam
        pass

    def _send(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        state: StageState = self.server.state  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, "text/html; charset=utf-8", _PAGE.encode("utf-8"))
        elif path == "/state":
            self._send(200, "application/json",
                       json.dumps(state.snapshot()).encode("utf-8"))
        elif path == "/avatar":
            self._send_image(state.avatar_path, "image/png")
        elif path == "/art":
            url = state.track.album_art_url
            self._send_image(nowplaying.art_path_for(url) if url else None, "image/jpeg")
        else:
            self._send(404, "text/plain", b"not found")

    def _send_image(self, path: Optional[str], ctype: str):
        if path and os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    self._send(200, ctype, fh.read())
                return
            except OSError:
                pass
        self._send(404, "text/plain", b"no image")


class WebOverlay:
    def __init__(self, state: StageState, port: int = 8770) -> None:
        self.state = state
        self.port = port
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> None:
        if self._server:
            return
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._server.state = self.state  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="weboverlay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/"
