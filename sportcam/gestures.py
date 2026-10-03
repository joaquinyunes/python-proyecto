"""Leer un número mostrando los dedos a la cámara (0-10, con una o dos manos).

Pipeline:  frame -> HandTracker (MediaPipe) -> dedos levantados por mano -> suma
           -> StableReading (hay que sostenerlo ~0.8 s) -> NumberAssembler (números de 2 cifras)

La geometría de los dedos usa distancias y ángulos 3D, no "arriba/abajo", así que funciona
con la mano inclinada. Los umbrales están en FingerConfig por si necesitás calibrarlos.
"""
from __future__ import annotations

import urllib.request
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HAND_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                  "hand_landmarker/float16/latest/hand_landmarker.task")
DEFAULT_MODEL_PATH = Path("models/hand_landmarker.task")

# Topología de los 21 puntos de la mano (para dibujar el esqueleto).
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
    (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]
# (MCP, PIP, TIP) de cada dedo que no es el pulgar.
FINGERS = {"indice": (5, 6, 8), "medio": (9, 10, 12), "anular": (13, 14, 16), "menique": (17, 18, 20)}


@dataclass(frozen=True)
class FingerConfig:
    straight_deg: float = 150.0   # ángulo mínimo en la articulación para considerar el dedo estirado
    thumb_out_ratio: float = 0.65  # pulgar: distancia punta->base del índice / tamaño de la palma


def _angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Ángulo en grados en el punto b entre los segmentos b->a y b->c."""
    v1, v2 = a - b, c - b
    denom = np.linalg.norm(v1) * np.linalg.norm(v2)
    if denom < 1e-9:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / denom, -1.0, 1.0))))


def fingers_up(lm: np.ndarray, cfg: FingerConfig = FingerConfig()) -> dict[str, bool]:
    """Qué dedos están estirados. `lm`: array (21, 3) con los landmarks de MediaPipe."""
    lm = np.asarray(lm, dtype=float)
    wrist = lm[0]
    palm = np.linalg.norm(lm[9] - wrist)   # muñeca -> base del dedo medio
    up = {}
    for name, (mcp, pip, tip) in FINGERS.items():
        straight = _angle(lm[mcp], lm[pip], lm[tip]) > cfg.straight_deg
        farther = np.linalg.norm(lm[tip] - wrist) > np.linalg.norm(lm[pip] - wrist)
        up[name] = bool(straight and farther)
    thumb_straight = _angle(lm[2], lm[3], lm[4]) > cfg.straight_deg
    thumb_out = palm > 1e-9 and np.linalg.norm(lm[4] - lm[5]) / palm > cfg.thumb_out_ratio
    up["pulgar"] = bool(thumb_straight and thumb_out)
    return up


def count_fingers(lm: np.ndarray, cfg: FingerConfig = FingerConfig()) -> int:
    return sum(fingers_up(lm, cfg).values())


@dataclass
class HandReading:
    landmarks: np.ndarray        # (21, 3) en píxeles del frame analizado
    fingers: int
    fingers_detail: dict[str, bool]
    wrist: tuple[float, float]   # (x, y) en píxeles del frame analizado


def total_fingers(readings: list[HandReading]) -> int | None:
    """Suma de dedos de todas las manos visibles; None si no hay manos (distinto de 0 = puño)."""
    return sum(r.fingers for r in readings) if readings else None


def ensure_hand_model(path: str | Path = DEFAULT_MODEL_PATH) -> Path:
    """Devuelve la ruta del modelo de manos, descargándolo la primera vez (~8 MB)."""
    path = Path(path)
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(HAND_MODEL_URL, path)
    except OSError as e:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"No pude descargar el modelo de manos ({e}). Bajalo a mano desde "
                           f"{HAND_MODEL_URL} y guardalo en {path}") from e
    return path


class HandTracker:
    """Detecta manos con MediaPipe y cuenta dedos. Una instancia por cámara/stream."""

    def __init__(self, model_path: str | Path | None = None, max_hands: int = 2,
                 cfg: FingerConfig = FingerConfig()):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        self._cfg = cfg
        self._last_ms = -1
        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(
                model_asset_path=str(ensure_hand_model(model_path or DEFAULT_MODEL_PATH))),
            running_mode=vision.RunningMode.VIDEO, num_hands=max_hands,
            min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5)
        self._landmarker = vision.HandLandmarker.create_from_options(options)

    def read(self, frame_bgr: np.ndarray, t_seconds: float) -> list[HandReading]:
        """Analiza un frame. `t_seconds` debe crecer (usá time.monotonic())."""
        import cv2

        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        ms = max(self._last_ms + 1, int(t_seconds * 1000))   # MediaPipe exige timestamps crecientes
        self._last_ms = ms
        result = self._landmarker.detect_for_video(
            self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ms)
        out = []
        for hand in result.hand_landmarks:
            lm = np.array([[p.x * w, p.y * h, p.z * w] for p in hand])
            detail = fingers_up(lm, self._cfg)
            out.append(HandReading(lm, sum(detail.values()), detail, (float(lm[0, 0]), float(lm[0, 1]))))
        return out

    def close(self) -> None:
        self._landmarker.close()


class StableReading:
    """Convierte lecturas ruidosas frame a frame en una decisión: "sostuvo el número N".

    Emite N una sola vez cuando domina (>= min_share) durante `hold_s` segundos. Para volver a
    emitir el mismo número hay que cambiar de gesto o bajar las manos. None = sin manos.
    """

    def __init__(self, hold_s: float = 0.8, min_share: float = 0.8):
        self.hold_s, self.min_share = hold_s, min_share
        self._buf: deque[tuple[float, int | None]] = deque()
        self._armed_for: int | None = None   # último valor emitido (None = listo para emitir)
        self._emitted = False

    def reset(self) -> None:
        self._buf.clear()
        self._emitted = False
        self._armed_for = None

    def update(self, value: int | None, t: float) -> int | None:
        self._buf.append((t, value))
        while self._buf and t - self._buf[0][0] > self.hold_s:
            self._buf.popleft()
        if self._buf[0][0] > t - 0.9 * self.hold_s:      # todavía no hay 0.9*hold_s de historia
            return None
        counts = Counter(v for _, v in self._buf)
        dominant, n = counts.most_common(1)[0]
        if n / len(self._buf) < self.min_share:
            return None
        if dominant is None:                              # manos abajo: se rearma
            self._emitted = False
            return None
        if self._emitted and dominant == self._armed_for:
            return None
        self._emitted, self._armed_for = True, dominant
        return dominant


class NumberAssembler:
    """Arma números de hasta `max_digits` cifras a partir de dígitos sostenidos uno a uno.

    Ej.: mostrar 1, luego puño (0) -> 10. El número se confirma tras `commit_after` s sin
    nuevos dígitos.
    """

    def __init__(self, max_digits: int = 2, commit_after: float = 1.5):
        self.max_digits, self.commit_after = max_digits, commit_after
        self._digits: list[int] = []
        self._last_t = 0.0

    @property
    def text(self) -> str:
        return "".join(map(str, self._digits))

    def reset(self) -> None:
        self._digits.clear()

    def push(self, digit: int, t: float) -> None:
        if not 0 <= digit <= 9:
            return   # 10 dedos (dos manos abiertas) no es una cifra
        if len(self._digits) >= self.max_digits:
            self._digits.clear()   # empieza otro número
        self._digits.append(digit)
        self._last_t = t

    def poll(self, t: float) -> int | None:
        """Devuelve el número terminado (y limpia) si pasó el tiempo de confirmación."""
        if self._digits and t - self._last_t >= self.commit_after:
            number = int(self.text)
            self._digits.clear()
            return number
        return None
