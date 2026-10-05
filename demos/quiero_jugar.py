"""Sección "Quiero jugar" por consola.

    python demos/quiero_jugar.py publicar Ana padel "2026-10-08 20:00" --lugar "Cancha 2" --con Beto
    python demos/quiero_jugar.py publicar Carla futbol "2026-10-08 21:00" --nombre "Los Pibes"
    python demos/quiero_jugar.py ver [--deporte futbol] [--dia 2026-10-08]
    python demos/quiero_jugar.py sumarse 2 Dario            # unirse al anuncio 2
    python demos/quiero_jugar.py emparejar 1 3              # armar el partido entre dos equipos completos
Los jugadores tienen que estar registrados (se crean solos si no existen).
"""
import argparse
import sys
import time
from datetime import date, datetime

import _comun  # deja listo el sys.path para importar sportcam

from sportcam.lobby import Lobby
from sportcam.sports import SPORTS
from sportcam.store import Store

del _comun


def jugador(store, nombre):
    return store.find_player(nombre) or store.add_player(nombre)


def mostrar(titulo, items, store):
    print(f"\n== {titulo} ({len(items)}) ==")
    for x in items:
        nombres = ", ".join(store.get_player(m)["name"] for m in x["members"])
        falta = "" if x["ready"] else f" | faltan {x['missing']}"
        print(f"#{x['id']} {time.strftime('%d/%m %H:%M', time.localtime(x['starts_at']))} "
              f"{SPORTS[x['sport']].name} - {x['name'] or 'sin nombre'} {x['size']}/{x['team_size']}{falta}"
              f" | {x['place'] or 'lugar a definir'} | {nombres}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--db", default="sportcam.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publicar"); p.add_argument("creador"); p.add_argument("deporte", choices=sorted(SPORTS))
    p.add_argument("cuando", help='"AAAA-MM-DD HH:MM" (hora local)'); p.add_argument("--nombre")
    p.add_argument("--lugar"); p.add_argument("--nota"); p.add_argument("--con", nargs="*", default=[])
    p.add_argument("--tamano", type=int, help="jugadores por equipo (por defecto el estándar del deporte)")
    v = sub.add_parser("ver"); v.add_argument("--deporte", choices=sorted(SPORTS)); v.add_argument("--dia")
    s = sub.add_parser("sumarse"); s.add_argument("anuncio", type=int); s.add_argument("jugador")
    e = sub.add_parser("emparejar"); e.add_argument("a", type=int); e.add_argument("b", type=int)
    args = ap.parse_args()
    store = Store(args.db)
    lobby = Lobby(store)
    try:
        if args.cmd == "publicar":
            cuando = datetime.strptime(args.cuando, "%Y-%m-%d %H:%M")
            pid = lobby.create_post(jugador(store, args.creador), args.deporte, cuando, name=args.nombre,
                                    place=args.lugar, note=args.nota, team_size=args.tamano,
                                    members=[jugador(store, n) for n in args.con])
            post = lobby.get_post(pid)
            print(f"Anuncio #{pid} publicado: {post['size']}/{post['team_size']} jugadores"
                  + ("" if post["ready"] else f" (faltan {post['missing']})"))
        elif args.cmd == "ver":
            dia = date.fromisoformat(args.dia) if args.dia else None
            res = lobby.list_posts(args.deporte, dia)
            mostrar("Equipos armados que buscan rival", res["armados"], store)
            mostrar("Equipos a los que les faltan jugadores", res["incompletos"], store)
        elif args.cmd == "sumarse":
            post = lobby.join_post(args.anuncio, jugador(store, args.jugador))
            print(f"Listo: {post['size']}/{post['team_size']}" + (" - ¡equipo completo!" if post["ready"] else ""))
        else:
            print(f"Partido {lobby.match_posts(args.a, args.b)} creado.")
    except ValueError as err:
        sys.exit(f"No se pudo: {err}")


if __name__ == "__main__":
    main()
