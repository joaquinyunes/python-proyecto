"""Une todo: cámara -> gesto -> evento del partido -> clip de replay.

Gramática de gestos (el número de dedos del primer gesto elige el evento del deporte):
  1. Mostrar "1" sostenido (en fútbol = gol). Si la cámara reconoce una cara conocida cerca de
     la mano, el evento se le asigna a esa persona y listo.
  2. Si no hay cara reconocida, se pide el número de camiseta con los dedos (1 y puño = 10) y se
     asigna al jugador de ese dorsal en el partido.
  3. Si el evento tiene `clip=True` (ej. gol), se guarda el replay de los últimos segundos.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .gestures import NumberAssembler, StableReading, total_fingers
from .sports import EventType, get_sport
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class Update:
    kind: str                 # 'info' | 'code' | 'digit' | 'event' | 'rejected' | 'timeout'
    message: str
    event_id: int | None = None


@dataclass
class _Pending:
    event: EventType
    since: float


class MatchSession:
    """Procesa frames de UN partido. `hands` debe tener `.read(frame, t) -> list[HandReading]`
    (ver gestures.HandTracker); `faces` es opcional (faces.FaceIdentifier); `replay` también."""

    def __init__(self, store: Store, match_id: int, *, hands, faces=None, replay=None,
                 clips_dir: str | Path = "clips", clip_seconds: float = 180.0,
                 hold_s: float = 0.8, dorsal_timeout: float = 10.0, dorsal_commit_s: float = 1.5,
                 max_face_distance: float = 3.0):
        match = store.get_match(match_id)
        if match is None:
            raise ValueError(f"partido {match_id} no existe")
        self.store, self.match_id = store, match_id
        self.sport = get_sport(match["sport"])
        self.hands, self.faces, self.replay = hands, faces, replay
        self.clips_dir, self.clip_seconds = Path(clips_dir), clip_seconds
        self.dorsal_timeout, self.max_face_distance = dorsal_timeout, max_face_distance
        self._stable = StableReading(hold_s=hold_s)
        self._number = NumberAssembler(max_digits=2, commit_after=dorsal_commit_s)
        self._pending: _Pending | None = None
        self.errors: list[str] = []     # fallos al guardar clips (el partido sigue funcionando)
        self.last_readings: list = []   # manos del último frame (en coordenadas espejadas, para dibujar)

    # ------------------------------------------------------------------ estado
    @property
    def status(self) -> str:
        """Texto corto para mostrar sobre el video."""
        if self._pending is None:
            return "Mostrá un número con los dedos"
        typed = self._number.text
        return f"{self._pending.event.label}: número de camiseta {typed or '...'}"

    # ----------------------------------------------------------------- proceso
    def process(self, frame_bgr: np.ndarray, t: float | None = None) -> list[Update]:
        t = time.monotonic() if t is None else t
        if self.replay is not None:
            self.replay.push(frame_bgr, t)          # siempre el frame real, sin espejar

        # La mano se analiza sobre la imagen espejada (como un espejo), pero el replay
        # y el reconocimiento de caras usan el frame original.
        mirrored = cv2.flip(frame_bgr, 1)
        readings = self.last_readings = self.hands.read(mirrored, t)
        digit = self._stable.update(total_fingers(readings), t)

        updates: list[Update] = []
        if digit is not None:
            updates += self._on_digit(digit, frame_bgr, readings, t)
        updates += self._tick(t)
        return updates

    def _on_digit(self, digit: int, frame, readings, t: float) -> list[Update]:
        if self._pending is not None:               # esperando el dorsal
            if digit > 9:
                return [Update("info", "Para el dorsal usá de 0 a 9 dedos por cifra")]
            self._number.push(digit, t)
            return [Update("digit", f"Dorsal: {self._number.text}")]

        event = self.sport.by_code(digit)
        if event is None:
            return [Update("info", f"{digit} dedo(s): sin acción en {self.sport.name}")] if digit else []

        player_id = self._player_from_face(frame, readings)
        if player_id is not None:
            return [self._confirm(event, player_id, "cara")]
        self._pending = _Pending(event, t)
        self._number.reset()
        return [Update("code", f"{event.label}: mostrá tu número de camiseta")]

    def _tick(self, t: float) -> list[Update]:
        if self._pending is None:
            return []
        dorsal = self._number.poll(t)
        if dorsal is not None:
            event, self._pending = self._pending.event, None
            player_id = self.store.player_by_dorsal(self.match_id, dorsal)
            if player_id is None:
                return [Update("rejected", f"No hay jugador con dorsal {dorsal} en este partido")]
            return [self._confirm(event, player_id, f"dorsal {dorsal}")]
        if t - self._pending.since > self.dorsal_timeout:
            label, self._pending = self._pending.event.label, None
            self._number.reset()
            return [Update("timeout", f"{label}: se canceló (no se indicó jugador)")]
        return []

    # --------------------------------------------------------------- atribución
    def _player_from_face(self, frame, readings) -> int | None:
        """Jugador reconocido por cara más cercano a la mano que hizo el gesto."""
        if self.faces is None or not readings:
            return None
        known = [m for m in self.faces.identify(frame)
                 if m.player_id is not None and self.store.player_in_match(self.match_id, m.player_id)]
        if not known:
            return None
        width = frame.shape[1]
        # los landmarks vienen del frame espejado: se pasa la x al frame original
        hands = [(width - r.wrist[0], r.wrist[1]) for r in readings]

        def gap(m):
            top, right, bottom, left = m.box
            cx, cy = (left + right) / 2, (top + bottom) / 2
            size = max(right - left, 1)
            return min(np.hypot(cx - hx, cy - hy) for hx, hy in hands) / size

        best = min(known, key=gap)
        return best.player_id if gap(best) <= self.max_face_distance else None

    # ------------------------------------------------------------------ eventos
    def _confirm(self, event: EventType, player_id: int, via: str) -> Update:
        event_id = self.store.add_event(self.match_id, player_id, event.key)
        name = self.store.get_player(player_id)["name"]
        msg = f"{event.label} de {name} (por {via})"
        if event.clip and self.replay is not None:
            self._save_clip(event, event_id, player_id)
            msg += " - guardando replay"
        return Update("event", msg, event_id)

    def _save_clip(self, event: EventType, event_id: int, player_id: int) -> None:
        rel = Path(f"partido_{self.match_id}") / f"evento_{event_id}_{event.key}_jugador_{player_id}.mp4"
        future = self.replay.save_clip(self.clips_dir / rel, seconds=self.clip_seconds)

        def done(f):
            try:
                _, seconds = f.result()
                self.store.add_clip(event_id, self.match_id, player_id, rel.as_posix(), seconds)
            except Exception as e:      # noqa: BLE001 - un clip fallido no debe tumbar el partido
                msg = f"no se pudo guardar el clip del evento {event_id}: {e}"
                self.errors.append(msg)
                log.error(msg)

        future.add_done_callback(done)

    def close(self) -> None:
        """Espera a que terminen de guardarse los clips pendientes."""
        if self.replay is not None:
            self.replay.close(wait=True)
