"""Códigos de partido: lo que pasa al alquilar la cancha.

    python demos/codigos.py crear --deporte futbol          # el dueño: genera el partido y el código
    python demos/codigos.py unirse ABC234 "Ana" A 10         # cada jugador: canjea el código
    python demos/partido.py --partido 1                      # en la cancha: arranca con esos jugadores
"""
import argparse
import sys

import _comun  # deja listo el sys.path para importar sportcam

del _comun

from sportcam.sports import SPORTS
from sportcam.store import Store


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--db", default="sportcam.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crear"); c.add_argument("--deporte", default="futbol", choices=sorted(SPORTS))
    c.add_argument("--horas", type=float, default=4.0, help="cuánto dura el código")
    u = sub.add_parser("unirse"); u.add_argument("codigo"); u.add_argument("nombre")
    u.add_argument("equipo", choices=["A", "B"]); u.add_argument("dorsal", type=int)
    args = ap.parse_args()
    store = Store(args.db)
    if args.cmd == "crear":
        m = store.create_match(args.deporte)
        print(f"Partido {m} creado. Código para los jugadores: {store.create_match_code(m, ttl_hours=args.horas)}")
        return
    pid = store.find_player(args.nombre)
    if pid is None:
        sys.exit(f"'{args.nombre}' no está registrado. Registralo primero (demos/caras.py registrar).")
    try:
        m = store.join_with_code(args.codigo, pid, args.equipo, args.dorsal)
    except ValueError as e:
        sys.exit(f"No se pudo: {e}")
    print(f"{args.nombre} quedó anotado en el partido {m} con el número {args.dorsal} (equipo {args.equipo}).")


if __name__ == "__main__":
    main()
