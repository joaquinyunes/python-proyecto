"""Utilidades compartidas por las demos: fuente de video y ventana."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # para `import sportcam` sin instalar

import cv2  # noqa: E402

from sportcam.gestures import HAND_CONNECTIONS  # noqa: E402


def add_video_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--camara", type=int, default=0, help="número de cámara (0 = la primera)")
    ap.add_argument("--video", help="usar un archivo de video en vez de la cámara")
    ap.add_argument("--sin-ventana", action="store_true", help="no abrir ventana (solo texto en consola)")


def frames(args):
    """Genera (frame, t) con t en segundos. Con --video respeta los fps del archivo."""
    cap = cv2.VideoCapture(args.video if args.video else args.camara)
    if not cap.isOpened():
        sys.exit("No pude abrir la cámara/video. Probá con --camara 1 o revisá los permisos.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield frame, (n / fps if args.video else time.monotonic())
            n += 1
    finally:
        cap.release()


class Ventana:
    """Muestra frames y detecta la tecla q. Con sin_ventana=True no hace nada."""

    def __init__(self, title: str, enabled: bool = True):
        self.title, self.enabled = title, enabled

    def show(self, img) -> bool:
        """True si hay que seguir; False si el usuario apretó q / Esc."""
        if not self.enabled:
            return True
        cv2.imshow(self.title, img)
        return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)

    def close(self):
        if self.enabled:
            cv2.destroyAllWindows()


def draw_hands(img, readings):
    """Esqueleto de la mano y dedos levantados, sobre un frame ya espejado."""
    for r in readings:
        pts = [(int(x), int(y)) for x, y, _ in r.landmarks]
        for a, b in HAND_CONNECTIONS:
            cv2.line(img, pts[a], pts[b], (0, 255, 0), 2)
        for p in pts:
            cv2.circle(img, p, 3, (0, 0, 255), -1)
        cv2.putText(img, str(r.fingers), (pts[0][0] - 10, pts[0][1] + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 0), 3)


def text(img, msg, y, scale=0.8, color=(255, 255, 255)):
    cv2.putText(img, msg, (12, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4)
    cv2.putText(img, msg, (12, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)
