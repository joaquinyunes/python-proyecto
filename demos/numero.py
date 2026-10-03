"""PRUEBA 1 - Leer un número con la mano.

    python demos/numero.py

Mostrá entre 0 y 10 dedos (con una o dos manos). Sostené el número ~1 segundo para que quede
"confirmado". Para números de 2 cifras mostrá una cifra, bajá la mano, mostrá la otra
(ej. 1 y después puño = 10) y esperá. Con --debug se ve qué dedo detecta como levantado.
Salir: q o Esc.
"""
import argparse

import cv2
from _comun import Ventana, add_video_args, draw_hands, frames, text

from sportcam.gestures import HandTracker, NumberAssembler, StableReading, total_fingers


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    add_video_args(ap)
    ap.add_argument("--debug", action="store_true", help="mostrar el detalle de cada dedo")
    args = ap.parse_args()

    tracker, stable, number = HandTracker(), StableReading(), NumberAssembler()
    win, confirmed, shown_until = Ventana("Numero con la mano (q = salir)", not args.sin_ventana), None, 0
    try:
        for frame, t in frames(args):
            view = cv2.flip(frame, 1)                      # espejo, como mirarse en un espejo
            readings = tracker.read(view, t)
            total = total_fingers(readings)
            digit = stable.update(total, t)
            if digit is not None:
                print(f"[confirmado] {digit} dedos")
                confirmed, shown_until = digit, t + 2.0
                number.push(digit, t)
            done = number.poll(t)
            if done is not None:
                print(f"[número] {done}")

            draw_hands(view, readings)
            text(view, f"Dedos: {'-' if total is None else total}", 40, 1.1, (0, 255, 255))
            if confirmed is not None and t < shown_until:
                text(view, f"CONFIRMADO: {confirmed}", 85, 1.1, (0, 255, 0))
            if number.text:
                text(view, f"Numero armado: {number.text}", 125, 0.9, (0, 200, 255))
            if args.debug:
                for i, r in enumerate(readings):
                    up = ", ".join(k for k, v in r.fingers_detail.items() if v) or "ninguno"
                    text(view, f"Mano {i + 1}: {up}", 165 + 30 * i, 0.7)
            if not win.show(view):
                break
    finally:
        tracker.close()
        win.close()


if __name__ == "__main__":
    main()
