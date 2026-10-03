"""PRUEBA 2 - Registrar y reconocer jugadores por la cara.

    python demos/caras.py registrar "Ana"     # pide consentimiento y toma ~6 fotos de tu cara
    python demos/caras.py reconocer           # muestra el nombre de quien aparezca en cámara
    python demos/caras.py borrar "Ana"        # elimina los datos faciales de esa persona

Solo se guardan vectores numéricos (128 números por toma), nunca fotos.
"""
import argparse
import sys

import cv2
from _comun import Ventana, add_video_args, frames, text

from sportcam.faces import FaceIdentifier
from sportcam.store import Store


def registrar(args, store):
    print(f"Se va a registrar la cara de '{args.nombre}' para identificarla en los partidos.")
    print("Se guarda solo un vector numérico (no la foto) y se puede borrar cuando quiera.")
    if input("¿La persona acepta? Escribí SI para continuar: ").strip().upper() != "SI":
        sys.exit("Cancelado: sin consentimiento no se registra la cara.")
    pid = store.find_player(args.nombre) or store.add_player(args.nombre, face_consent=True)
    if not store.get_player(pid)["face_consent"]:
        sys.exit(f"'{args.nombre}' ya existe sin consentimiento facial; borrá y creá de nuevo.")

    ident, win, muestras, last = FaceIdentifier(), Ventana("Registro (q = cancelar)", not args.sin_ventana), 0, 0.0
    objetivo = args.muestras
    try:
        for frame, t in frames(args):
            view = cv2.flip(frame, 1)
            if t - last > 0.7:            # una toma cada ~0.7 s, así se puede mover un poco la cabeza
                caras = ident.faces(frame)
                if len(caras) == 1:
                    store.save_face(pid, caras[0][1])
                    muestras, last = muestras + 1, t
                    print(f"toma {muestras}/{objetivo}")
            text(view, f"Mira a la camara y mueve un poco la cabeza: {muestras}/{objetivo}", 40, 0.7)
            if muestras >= objetivo or not win.show(view):
                break
    finally:
        win.close()
    print(f"Listo: {muestras} tomas guardadas para '{args.nombre}' (id {pid})." if muestras
          else "No se pudo registrar ninguna toma.")


def reconocer(args, store):
    ident = FaceIdentifier()
    ident.set_gallery(store.load_faces())
    nombres = {p["id"]: p["name"] for p in store.list_players()}
    win = Ventana("Reconocer (q = salir)", not args.sin_ventana)
    try:
        for i, (frame, t) in enumerate(frames(args)):
            if i % 3:                      # reconocer cada 3 frames alcanza y ahorra CPU
                continue
            view = cv2.flip(frame, 1)
            w = frame.shape[1]
            for m in ident.identify(frame):
                top, right, bottom, left = m.box
                label = f"{nombres.get(m.player_id, '?')} ({m.distance:.2f})" if m.player_id else f"desconocido ({m.distance:.2f})"
                color = (0, 255, 0) if m.player_id else (0, 0, 255)
                cv2.rectangle(view, (w - right, top), (w - left, bottom), color, 2)
                text(view, label, max(top - 8, 20), 0.6, color)
                if args.sin_ventana:
                    print(label)
            if not win.show(view):
                break
    finally:
        win.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--db", default="sportcam.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("registrar"); r.add_argument("nombre"); r.add_argument("--muestras", type=int, default=6)
    add_video_args(r)
    c = sub.add_parser("reconocer"); add_video_args(c)
    b = sub.add_parser("borrar"); b.add_argument("nombre")
    args = ap.parse_args()
    store = Store(args.db)
    if args.cmd == "registrar":
        registrar(args, store)
    elif args.cmd == "reconocer":
        reconocer(args, store)
    else:
        pid = store.find_player(args.nombre)
        if pid is None:
            sys.exit(f"No existe '{args.nombre}'.")
        store.forget_face(pid)
        print(f"Datos faciales de '{args.nombre}' eliminados.")


if __name__ == "__main__":
    main()
