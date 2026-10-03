"""Reglas de torneo: cómo se decide quién gana.

Un torneo define `rank_by`: lista de métricas en orden de prioridad (todas "más es mejor").
Ejemplo: ("wins", "gol") = gana el que tiene más partidos ganados; si empatan, más goles.
"""
from __future__ import annotations

from .sports import Sport

BUILTIN_METRICS = {
    "wins": "Partidos ganados",
    "losses": "Partidos perdidos",
    "draws": "Partidos empatados",
    "matches": "Partidos jugados",
    "win_rate": "% de victorias",
    "points": "Puntos anotados",
    "league_points": "Puntos de tabla (3 ganar, 1 empatar)",
}


def available_metrics(sport: Sport) -> dict[str, str]:
    """Métricas válidas para un torneo de este deporte: las generales + un conteo por evento."""
    metrics = dict(BUILTIN_METRICS)
    metrics.update({e.key: f"{e.label} (cantidad)" for e in sport.events})
    return metrics


def validate_rank_by(sport: Sport, rank_by: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    rank_by = tuple(rank_by)
    if not rank_by:
        raise ValueError("rank_by no puede estar vacío")
    valid = available_metrics(sport)
    bad = [m for m in rank_by if m not in valid]
    if bad:
        raise ValueError(f"métricas inválidas {bad}. Opciones: {sorted(valid)}")
    return rank_by


def metric_value(stats: dict, metric: str) -> float:
    if metric in BUILTIN_METRICS:
        return stats[metric]
    return stats["events"].get(metric, 0)


def rank(stats_by_player: dict[int, dict], rank_by: tuple[str, ...]) -> list[dict]:
    """Ordena jugadores por `rank_by`. Desempate final: id más bajo (estable y predecible)."""
    def sort_key(pid: int):
        s = stats_by_player[pid]
        return tuple(-metric_value(s, m) for m in rank_by) + (pid,)

    ordered = sorted(stats_by_player, key=sort_key)
    return [{"position": i + 1, "player_id": pid, **stats_by_player[pid]}
            for i, pid in enumerate(ordered)]
