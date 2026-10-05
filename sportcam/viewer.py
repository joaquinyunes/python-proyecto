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
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from urllib.parse import parse_qs, urlsplit

from .lobby import Lobby
from .sports import SPORTS, get_sport
from .tournaments import available_metrics
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
            url = urlsplit(self.path)
            path, query = url.path, parse_qs(url.query)
            if path == "/quiero-jugar":
                return self._lobby(query)
            if path == "/":
                return self._index()
            if m := re.fullmatch(r"/jugador/(\d+)", path):
                return self._player(int(m[1]))
            if m := re.fullmatch(r"/torneo/(\d+)", path):
                return self._tournament(int(m[1]))
            if m := re.fullmatch(r"/partido/(\d+)", path):
                return self._match(int(m[1]))
            if path.startswith("/clips/"):
                return self._clip(path[len("/clips/"):])
            self._send(404, _page("No encontrado", "<h1>404</h1>"))

        def _index(self):
            rows = "".join(
                f"<tr><td><a href='/jugador/{p['id']}'>{html.escape(p['name'])}</a></td>"
                f"<td>{store.player_stats(p['id'])['matches']}</td>"
                f"<td>{len(store.list_clips(player_id=p['id']))}</td></tr>"
                for p in store.list_players())
            torneos = "".join(
                f"<li><a href='/torneo/{t['id']}'>{html.escape(t['name'])}</a> ({html.escape(t['sport'])})</li>"
                for t in store.list_tournaments())
            self._send(200, _page("Jugadores", "<h1>Jugadores</h1><table><tr><th>Jugador<th>Partidos"
                                  f"<th>Jugadas</tr>{rows}</table><p><a href='/quiero-jugar'><b>Quiero jugar</b></a></p><h2>Torneos</h2><ul>{torneos or '<li>Ninguno todavía</li>'}</ul>"))

        def _lobby(self, query: dict):
            sport = (query.get("deporte") or [""])[0] or None
            try:
                day = date.fromisoformat(query["dia"][0]) if query.get("dia") and query["dia"][0] else None
            except ValueError:
                day = None
            if sport not in SPORTS:
                sport = None
            posts = Lobby(store).list_posts(sport, day)

            def table(items, vacio):
                if not items:
                    return f"<p>{vacio}</p>"
                rows = "".join(
                    f"<tr><td>{time.strftime('%d/%m %H:%M', time.localtime(x['starts_at']))}"
                    f"<td>{html.escape(SPORTS[x['sport']].name)}"
                    f"<td>{html.escape(x['name'] or 'Equipo de ' + store.get_player(x['creator_id'])['name'])}"
                    f"<td>{x['size']}/{x['team_size']}"
                    f"<td>{'' if x['ready'] else 'faltan ' + str(x['missing'])}"
                    f"<td>{html.escape(x['place'] or '')}<td>{html.escape(x['note'] or '')}</tr>"
                    for x in items)
                return ("<table><tr><th>Cuándo<th>Deporte<th>Equipo<th>Jugadores<th><th>Lugar<th>Nota</tr>"
                        f"{rows}</table>")

            opciones = "".join(
                f"<option value='{k}'{' selected' if k == sport else ''}>{html.escape(v.name)}</option>"
                for k, v in SPORTS.items())
            form = (f"<form method=get><select name=deporte><option value=''>Todos los deportes</option>{opciones}"
                    f"</select> <input type=date name=dia value='{day.isoformat() if day else ''}'> "
                    "<button>Filtrar</button></form>")
            body = (f"<p><a href='/'>&larr; Inicio</a></p><h1>Quiero jugar</h1>{form}"
                    f"<h2>Equipos armados que buscan rival ({len(posts['armados'])})</h2>"
                    f"{table(posts['armados'], 'No hay equipos armados para ese filtro.')}"
                    f"<h2>Equipos a los que les faltan jugadores ({len(posts['incompletos'])})</h2>"
                    f"{table(posts['incompletos'], 'No hay equipos buscando jugadores para ese filtro.')}")
            self._send(200, _page("Quiero jugar", body))

        def _name(self, pid: int) -> str:
            return html.escape(store.get_player(pid)["name"])

        def _tournament(self, tid: int):
            t = store.get_tournament(tid)
            if t is None:
                return self._send(404, _page("No encontrado", "<h1>Torneo no encontrado</h1>"))
            metrics = available_metrics(get_sport(t["sport"]))
            regla = " &rarr; ".join(html.escape(metrics[m]) for m in t["rank_by"])
            filas = "".join(
                f"<tr><td>{r['position']}<td><a href='/jugador/{r['player_id']}'>{self._name(r['player_id'])}</a>"
                f"<td>{r['matches']}<td>{r['wins']}<td>{r['draws']}<td>{r['losses']}<td>{r['points']}</tr>"
                for r in store.leaderboard(tid))
            cruces = "".join(
                f"<tr><td>{f['round']}<td>{self._name(f['a_id'])} vs {self._name(f['b_id'])}<td>{f['status']}"
                f"<td>{'<a href=/partido/%d>ver</a>' % f['match_id'] if f['match_id'] else ''}</tr>"
                for f in store.tournament_fixtures(tid))
            equipos = ""
            if store.list_teams(tid):
                names = {x["id"]: html.escape(x["name"]) for x in store.list_teams(tid)}
                filas_e = "".join(
                    f"<tr><td>{r['position']}<td>{html.escape(r['name'])}<td>{r['played']}<td>{r['wins']}"
                    f"<td>{r['draws']}<td>{r['losses']}<td>{r['for']}:{r['against']}<td>{r['points']}</tr>"
                    for r in store.team_standings(tid))
                cruces_e = "".join(
                    f"<tr><td>{f['round']}<td>{names[f['a_team']]} vs {names[f['b_team']]}<td>{f['status']}"
                    f"<td>{'<a href=/partido/%d>ver</a>' % f['match_id'] if f['match_id'] else ''}</tr>"
                    for f in store.team_fixtures(tid))
                equipos = ("<h2>Equipos</h2><table><tr><th>#<th>Equipo<th>PJ<th>G<th>E<th>P<th>Goles<th>Pts</tr>"
                           f"{filas_e}</table><table><tr><th>Ronda<th>Cruce<th>Estado<th></tr>{cruces_e}</table>")
            campeon = f"<p><b>Campeón: {self._name(t['champion_id'])}</b></p>" if t["champion_id"] else ""
            self._send(200, _page(t["name"], f"<p><a href='/'>&larr; Inicio</a></p><h1>{html.escape(t['name'])}</h1>"
                                  f"{campeon}<p>Gana: {regla}</p><table><tr><th>#<th>Jugador<th>PJ<th>G<th>E<th>P"
                                  f"<th>Pts</tr>{filas}</table><h2>Calendario</h2><table><tr><th>Ronda<th>Cruce"
                                  f"<th>Estado<th></tr>{cruces}</table>{equipos}"))

        def _match(self, mid: int):
            if store.get_match(mid) is None:
                return self._send(404, _page("No encontrado", "<h1>Partido no encontrado</h1>"))
            sm = store.match_summary(mid)
            labels = {e.key: e.label for e in get_sport(sm["sport"]).events}
            filas = "".join(
                f"<tr><td>{'&#11088; ' if p['player_id'] == sm['mvp'] else ''}"
                f"<a href='/jugador/{p['player_id']}'>{html.escape(p['name'])}</a><td>{p['side']}"
                f"<td>{'' if p['dorsal'] is None else p['dorsal']}"
                f"<td>{html.escape(', '.join(f'{labels[k]}: {n}' for k, n in sorted(p['events'].items())) or '-')}</tr>"
                for p in sm["players"])
            estado = "Terminado" if sm["finished"] else "En juego"
            self._send(200, _page(f"Partido {mid}", f"<p><a href='/'>&larr; Inicio</a></p><h1>Partido {mid}: "
                                  f"{sm['score']['A']} - {sm['score']['B']}</h1><p>{estado} (&#11088; = figura)</p>"
                                  f"<table><tr><th>Jugador<th>Equipo<th>N&deg;<th>Acciones</tr>{filas}</table>"))

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
