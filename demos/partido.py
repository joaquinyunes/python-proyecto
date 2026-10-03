"""PRUEBA 3 - Partido completo: gestos + caras + replay de 3 minutos + estadísticas.

    python demos/partido.py --deporte futbol --jugador Ana:A:10 --jugador Beto:B:7
    python demos/partido.py --partido 1          # partido creado con código (demos/codigos.py)

Cada --jugador es  nombre:equipo(A/B):dorsal.  Si el nombre no existe en la base, se crea.
Si registraste su cara (demos/caras.py registrar), se reconoce solo; si no, usa el dorsal.

Cómo se usa durante el partido (el gesto lo hace quien jugó):
  1) Mostrá el número del evento sostenido ~1 s:   1 dedo = gol (fútbol), 2 = asistencia, 3 = atajada
  2) Si la cámara no te reconoce, bajá la mano y mostrá tu número de camiseta (1 y puño = 10).
  3) En un gol se guarda el replay de los últimos 3 minutos y un resumen de 30 s.
  4) Dos manos abiertas (10 dedos) = deshacer el último evento.
Terminar el partido: q o Esc. Después: python -m sportcam.viewer
"""
import argparse
import sys

import cv2
from _comun import Ventana, add_video_args, draw_hands, frames, text

from sportcam.faces import FaceIdentifier
from sportcam.gestures import HandTracker
from sportcam.replay import ReplayBuffer
from sportcam.session import MatchSession
from sportcam.sports import SPORTS
from sportcam.store import Store


def parse_player(spec: str):
    try:
        name, side, dorsal = spec.rsplit(":", 2)
        if side.upper() not in ("A", "B"):
            raise ValueError
        return name.strip(), side.upper(), int(dorsal)
    except ValueError:
        sys.exit(f"--jugador '{spec}' inválido. Formato: nombre:A:10")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    add_video_args(ap)
    ap.add_argument("--deporte", default="futbol", choices=sorted(SPORTS))
    ap.add_argument("--jugador", action="append", default=[], metavar="NOMBRE:EQUIPO:DORSAL")
    ap.add_argument("--partido", type=int, help="usar un partido existente (ya con jugadores)")
    ap.add_argument("--db", default="sportcam.db")
    ap.add_argument("--clips", default="clips")
    ap.add_argument("--clip-segundos", type=float, default=180.0)
    ap.add_argument("--sin-caras", action="store_true", help="no usar reconocimiento facial")
    args = ap.parse_args()

    store = Store(args.db)
    nombres = {}
    if args.partido:
        match = args.partido
        info = store.get_match(match)
        if info is None or info["finished_at"] is not None:
            sys.exit(f"El partido {match} no existe o ya terminó.")
        args.deporte = info["sport"]
        nombres = {r["player_id"]: store.get_player(r["player_id"])["name"] for r in store._q(
            "SELECT player_id FROM match_players WHERE match_id=?", (match,))}
        if not nombres:
            sys.exit("Ese partido todavía no tiene jugadores: que canjeen el código primero.")
    else:
        if not args.jugador:
            sys.exit("Indicá --partido ID o al menos un --jugador nombre:A:10")
        match = store.create_match(args.deporte)
        for spec in args.jugador:
            name, side, dorsal = parse_player(spec)
            pid = store.find_player(name) or store.add_player(name)
            store.add_match_player(match, pid, side, dorsal)
            nombres[pid] = name

    faces = None
    if not args.sin_caras:
        gallery = {p: v for p, v in store.load_faces().items() if p in nombres}
        if gallery:
            faces = FaceIdentifier()
            faces.set_gallery(gallery)
            print(f"Reconocimiento facial activo para: {', '.join(nombres[p] for p in gallery)}")

    replay = ReplayBuffer(seconds=args.clip_segundos)
    session = MatchSession(store, match, hands=HandTracker(), faces=faces, replay=replay,
                           clips_dir=args.clips, clip_seconds=args.clip_segundos)
    win, avisos = Ventana("Partido (q = terminar)", not args.sin_ventana), []   # avisos: (hasta_t, texto)
    print(f"Partido {match} de {SPORTS[args.deporte].name} en curso. q para terminar.")
    try:
        for frame, t in frames(args):
            for u in session.process(frame, t):
                print(f"[{u.kind}] {u.message}")
                avisos.append((t + 4.0, u.message))
            view = cv2.flip(frame, 1)
            draw_hands(view, session.last_readings)
            score = store.match_score(match)
            text(view, f"A {score['A']} - {score['B']} B", 36, 1.0, (0, 255, 255))
            text(view, session.status, 72, 0.7)
            text(view, f"Replay en memoria: {replay.duration:.0f}s ({replay.memory_mb:.0f} MB)",
                 view.shape[0] - 14, 0.55, (200, 200, 200))
            avisos = [a for a in avisos if a[0] > t]
            for i, (_, msg) in enumerate(avisos[-3:]):
                text(view, msg, 110 + 30 * i, 0.65, (0, 255, 0))
            if not win.show(view):
                break
    finally:
        win.close()
        session.hands.close()
        print("Guardando replays pendientes...")
        session.close()

    winner = store.finish_match(match)
    print(f"\nPartido terminado. Marcador A {store.match_score(match)['A']} - {store.match_score(match)['B']} B"
          f" | Resultado: {'empate' if winner == 'D' else 'ganó ' + winner}")
    for pid, name in nombres.items():
        s = store.player_stats(pid, sport=args.deporte)
        print(f"  {name}: {s['events'] or 'sin eventos'} | {s['wins']}G {s['draws']}E {s['losses']}P")
    if session.errors:
        print("Avisos:", *session.errors, sep="\n  ")


if __name__ == "__main__":
    main()
