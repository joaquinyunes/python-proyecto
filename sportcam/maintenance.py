"""Limpieza periódica: los clips ocupan mucho, conviene borrarlos pasado un tiempo."""
from __future__ import annotations

from pathlib import Path

from .store import Store


def purge_old_clips(store: Store, clips_dir: str | Path, older_than_days: float = 30.0) -> int:
    """Borra archivos y registros de clips más viejos que `older_than_days`. Devuelve cuántos."""
    root = Path(clips_dir).resolve()
    n = 0
    for clip in store.expired_clips(older_than_days):
        target = (root / clip["path"]).resolve()
        if root in target.parents:           # nunca borrar fuera de la carpeta de clips
            target.unlink(missing_ok=True)
        store.delete_clip(clip["id"])
        n += 1
    return n
