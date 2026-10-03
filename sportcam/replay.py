"""Replay instantáneo: guarda los últimos N segundos de cámara y los exporta a mp4 al pedido.

Los frames se guardan comprimidos (JPEG) en memoria para que 3 minutos no ocupen GBs:
a 960 px de ancho y 25 fps son unos 200-300 MB (medido con fotos reales; más si la cámara
tiene mucho ruido). Bajá `fps` o `max_width` si necesitás menos.
El mp4 sale en H.264 (el único formato que reproduce cualquier navegador).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np


class ReplayBuffer:
    def __init__(self, seconds: float = 180.0, fps: float = 25.0, max_width: int = 960,
                 jpeg_quality: int = 75):
        self.seconds = seconds
        # Tope blando: acepta intervalos de >=75% del ideal. Con tope estricto, una cámara de
        # exactamente `fps` y algo de jitter perdería casi la mitad de los frames.
        self._min_dt = 0.75 / fps
        self._max_width = max_width
        self._jpeg = [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
        self._frames: deque[tuple[float, bytes]] = deque()
        self._bytes = 0
        self._last_t = float("-inf")
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="replay")

    # ------------------------------------------------------------------ captura
    def push(self, frame_bgr: np.ndarray, t: float | None = None) -> bool:
        """Agrega un frame. Devuelve False si se descartó por llegar más rápido que `fps`."""
        t = time.monotonic() if t is None else t
        if t - self._last_t < self._min_dt:
            return False
        h, w = frame_bgr.shape[:2]
        scale = min(1.0, self._max_width / w)
        new_w, new_h = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2   # H.264 pide medidas pares
        if (new_w, new_h) != (w, h):
            frame_bgr = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame_bgr, self._jpeg)
        if not ok:
            return False
        data = buf.tobytes()
        with self._lock:
            self._last_t = t
            self._frames.append((t, data))
            self._bytes += len(data)
            while self._frames and t - self._frames[0][0] > self.seconds:
                self._bytes -= len(self._frames.popleft()[1])
        return True

    @property
    def duration(self) -> float:
        """Segundos de video disponibles ahora mismo."""
        with self._lock:
            return self._frames[-1][0] - self._frames[0][0] if len(self._frames) > 1 else 0.0

    @property
    def memory_mb(self) -> float:
        return self._bytes / 1e6

    # ------------------------------------------------------------------ exportar
    def save_clip(self, path: str | Path, seconds: float | None = None) -> Future:
        """Exporta los últimos `seconds` (por defecto todo el buffer) a `path` sin frenar la cámara.

        Toma la foto del buffer YA (el momento del gol) y codifica en un hilo aparte.
        Devuelve un Future cuyo resultado es el Path del mp4 y su duración real: (Path, segundos).
        """
        with self._lock:
            frames = list(self._frames)
        if seconds is not None and frames:
            limit = frames[-1][0] - seconds
            frames = [f for f in frames if f[0] >= limit]
        return self._pool.submit(self._encode, Path(path), frames)

    @staticmethod
    def _encode(path: Path, frames: list[tuple[float, bytes]]) -> tuple[Path, float]:
        if len(frames) < 2:
            raise RuntimeError("no hay suficiente video en el buffer para armar el clip")
        from imageio_ffmpeg import write_frames   # import tardío: solo se necesita al exportar

        span = frames[-1][0] - frames[0][0]
        fps = min(60.0, max(5.0, (len(frames) - 1) / span))   # fps real: la cámara puede ir lenta
        first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        h, w = first.shape[:2]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part.mp4")
        writer = write_frames(
            str(tmp), (w, h), pix_fmt_in="bgr24", pix_fmt_out="yuv420p", fps=fps,
            codec="libx264", macro_block_size=2,
            output_params=["-crf", "23", "-preset", "veryfast", "-movflags", "+faststart"])
        try:
            writer.send(None)
            for _, data in frames:
                writer.send(cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR).tobytes())
        finally:
            writer.close()
        tmp.replace(path)    # el archivo aparece completo o no aparece (la web nunca ve uno a medias)
        return path, span

    def close(self, wait: bool = True) -> None:
        """Espera a que terminen los clips pendientes."""
        self._pool.shutdown(wait=wait)
