"""Página mínima para ver las jugadas de cada jugador (solo librería estándar).

    python -m sportcam.viewer --db sportcam.db --clips clips      ->  http://127.0.0.1:8000

Es una página de prueba para ver el resultado ya mismo. En tu app, usá Store.list_clips()
y serví los mp4 desde tu propio servidor.
"""
from __future__ import annotations

import argparse
import html
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .sports import SPORTS
from .store import Store

CSS = ("body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}"
       "table{border-collapse:collapse}td,th{padding:.3rem .8rem;border-bottom:1px solid #ddd;text-align:left}"
       "video{width:100%;max-width:640px;background:#000}.clip{margin:1.5rem 0}")


def _page(title: str, body: str) -> bytes:
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style>{body}").encode()


def make_handler(store: Store, clips_dir: Path):
    clips_root = clips_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # silencioso
            pass

        def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8",
                  extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_HEAD = lambda self: self.do_GET()   # noqa: E731

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                return self._index()
            if m := re.fullmatch(r"/jugador/(\d+)", path):
                return self._player(int(m[1]))
            if path.startswith("/clips/"):
                return self._clip(path[len("/clips/"):])
            self._send(404, _page("No encontrado", "<h1>404</h1>"))

        def _index(self):
            rows = "".join(
                f"<tr><td><a href='/jugador/{p['id']}'>{html.escape(p['name'])}</a></td>"
                f"<td>{store.player_stats(p['id'])['matches']}</td>"
                f"<td>{len(store.list_clips(player_id=p['id']))}</td></tr>"
                for p in store.list_players())
            self._send(200, _page("Jugadores", "<h1>Jugadores</h1><table><tr><th>Jugador<th>Partidos"
                                  f"<th>Jugadas</tr>{rows}</table>"))

        def _player(self, pid: int):
            player = store.get_player(pid)
            if player is None:
                return self._send(404, _page("No encontrado", "<h1>Jugador no encontrado</h1>"))
            s = store.player_stats(pid)
            labels = {e.key: e.label for sp in SPORTS.values() for e in sp.events}
            ev = "".join(f"<tr><td>{html.escape(labels.get(k, k))}<td>{n}</tr>" for k, n in sorted(s["events"].items()))
            clips = "".join(
                f"<div class=clip><b>{html.escape(labels.get(c['event_type'], c['event_type']))}</b> - "
                f"{time.strftime('%d/%m/%Y %H:%M', time.localtime(c['event_ts']))} "
                f"({'resumen' if c['kind'] == 'highlight' else 'últimos 3 min'}, partido {c['match_id']})<br><video controls preload=metadata "
                f"src='/clips/{html.escape(c['path'], quote=True)}'></video></div>"
                for c in store.list_clips(player_id=pid)) or "<p>Todavía no hay jugadas guardadas.</p>"
            body = (f"<p><a href='/'>&larr; Jugadores</a></p><h1>{html.escape(player['name'])}</h1>"
                    f"<p>Partidos: {s['matches']} | Ganados: {s['wins']} | Perdidos: {s['losses']} | "
                    f"Empatados: {s['draws']} | Torneos jugados: {s['tournaments_played']} | "
                    f"Torneos ganados: {s['tournaments_won']}</p>"
                    f"<table><tr><th>Estadística<th>Total</tr>{ev}</table><h2>Jugadas</h2>{clips}")
            self._send(200, _page(player["name"], body))

        def _clip(self, rel: str):
            from urllib.parse import unquote
            target = (clips_root / unquote(rel)).resolve()
            if clips_root not in target.parents or target.suffix != ".mp4" or not target.is_file():
                return self._send(404, _page("No encontrado", "<h1>404</h1>"))
            size = target.stat().st_size
            start, end, status = 0, size - 1, 200
            if rng := re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "")):
                a, b = rng.groups()
                start = int(a) if a else max(0, size - int(b or 0))
                end = min(int(b), size - 1) if a and b else size - 1
                if start > end or start >= size:
                    return self._send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                status = 206
            with open(target, "rb") as f:
                f.seek(start)
                data = f.read(end - start + 1)
            extra = {"Accept-Ranges": "bytes"}
            if status == 206:
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._send(status, data, "video/mp4", extra)

    return Handler


def serve(db: str = "sportcam.db", clips: str = "clips", host: str = "127.0.0.1",
          port: int = 8000) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(Store(db), Path(clips)))
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="sportcam.db")
    ap.add_argument("--clips", default="clips")
    ap.add_argument("--host", default="127.0.0.1", help="cambiá a 0.0.0.0 para verlo desde el celular")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    srv = serve(a.db, a.clips, a.host, a.port)
    print(f"Jugadas en http://{a.host}:{a.port}  (Ctrl+C para salir)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
