"""Identificar jugadores por la cara (dlib). Guarda vectores de 128 números, nunca fotos.

Uso típico:
    ident = FaceIdentifier()
    vecs = ident.enroll([frame1, frame2, ...])        # frames con UNA cara
    store.save_face(player_id, v) for v in vecs        # requiere consentimiento (ver Store)
    ident.set_gallery(store.load_faces())
    for m in ident.identify(frame): m.player_id, m.distance, m.box
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def _model_file(name: str) -> str:
    """Ruta de un modelo dentro de `face_recognition_models` SIN importar ese paquete
    (su __init__ usa pkg_resources, que está deprecado y falla en setuptools nuevos)."""
    spec = importlib.util.find_spec("face_recognition_models")
    if spec is None or not spec.submodule_search_locations:
        raise ImportError("Falta face_recognition_models. Instalá: pip install face_recognition_models")
    return str(Path(next(iter(spec.submodule_search_locations))) / "models" / name)


@dataclass(frozen=True)
class FaceMatch:
    player_id: int | None            # None = cara desconocida
    distance: float                  # menor = más parecido (la distancia al mejor candidato)
    box: tuple[int, int, int, int]   # (arriba, derecha, abajo, izquierda) en píxeles del frame


class FaceIdentifier:
    """tolerance: distancia máxima para aceptar una coincidencia. 0.6 es el valor clásico de
    dlib; 0.5 (por defecto) es más estricto: prefiere decir "no sé" antes que confundir a dos
    jugadores. `margin`: si el segundo mejor jugador está casi igual de cerca, es ambiguo."""

    def __init__(self, tolerance: float = 0.5, margin: float = 0.04, detect_width: int = 640):
        try:
            import dlib
        except ImportError as e:
            raise ImportError("Falta dlib. Instalá: pip install dlib-bin face_recognition_models") from e
        self._detector = dlib.get_frontal_face_detector()
        self._shape = dlib.shape_predictor(_model_file("shape_predictor_5_face_landmarks.dat"))
        self._encoder = dlib.face_recognition_model_v1(_model_file("dlib_face_recognition_resnet_model_v1.dat"))
        self.tolerance, self.margin, self._detect_width = tolerance, margin, detect_width
        self._gallery: dict[int, np.ndarray] = {}

    # ----------------------------------------------------------------- detección
    def faces(self, frame_bgr: np.ndarray) -> list[tuple[tuple[int, int, int, int], np.ndarray]]:
        """Todas las caras del frame: [(box, vector128)]. Box en coordenadas del frame original."""
        import cv2

        h, w = frame_bgr.shape[:2]
        scale = min(1.0, self._detect_width / w)
        small = cv2.resize(frame_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) \
            if scale < 1.0 else frame_bgr
        rgb = np.ascontiguousarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        out = []
        for rect in self._detector(rgb, 1):
            shape = self._shape(rgb, rect)
            vec = np.array(self._encoder.compute_face_descriptor(rgb, shape))
            box = (int(rect.top() / scale), int(rect.right() / scale),
                   int(rect.bottom() / scale), int(rect.left() / scale))
            out.append((box, vec))
        return out

    def enroll(self, frames: list[np.ndarray]) -> list[np.ndarray]:
        """Vectores faciales de frames con exactamente una cara. Ignora los que tengan 0 o varias.

        Pasá varias tomas (de frente, algo girado, distinta luz): mejora mucho el reconocimiento.
        """
        vecs = []
        for f in frames:
            found = self.faces(f)
            if len(found) == 1:
                vecs.append(found[0][1])
        if not vecs:
            raise ValueError("no se encontró ninguna cara única en las imágenes")
        return vecs

    # ------------------------------------------------------------ identificación
    def set_gallery(self, gallery: dict[int, list[np.ndarray]]) -> None:
        self._gallery = {pid: np.vstack(vs) for pid, vs in gallery.items() if len(vs)}

    def match(self, vec: np.ndarray) -> tuple[int | None, float]:
        """(player_id o None, distancia al mejor candidato)."""
        if not self._gallery:
            return None, float("inf")
        best = sorted(((float(np.linalg.norm(vs - vec, axis=1).min()), pid)
                       for pid, vs in self._gallery.items()))
        dist, pid = best[0]
        if dist > self.tolerance:
            return None, dist
        if len(best) > 1 and best[1][0] - dist < self.margin and best[1][0] <= self.tolerance:
            return None, dist     # dos jugadores igual de probables: mejor no adivinar
        return pid, dist

    def identify(self, frame_bgr: np.ndarray) -> list[FaceMatch]:
        return [FaceMatch(*self.match(vec), box) for box, vec in self.faces(frame_bgr)]
