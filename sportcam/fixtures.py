"""Calendarios de torneo: todos contra todos y llaves de eliminación (funciones puras)."""
from __future__ import annotations


def round_robin(ids: list[int], double: bool = False) -> list[list[tuple[int, int]]]:
    """Todos contra todos (método del círculo). Devuelve rondas de (local, visitante).
    Con cantidad impar, quien descansa en una ronda no aparece. `double`=ida y vuelta."""
    ids = list(ids)
    if len(set(ids)) != len(ids):
        raise ValueError("ids repetidos")
    if len(ids) < 2:
        raise ValueError("hacen falta al menos 2 jugadores")
    players = ids + [None] * (len(ids) % 2)
    n = len(players)
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = players[i], players[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))   # alterna localía
        rounds.append(pairs)
        players = [players[0]] + [players[-1]] + players[1:-1]
    if double:
        rounds += [[(b, a) for a, b in rd] for rd in rounds]
    return rounds


def knockout_first_round(seeds: list[int]) -> list[tuple[int, int | None]]:
    """Primera ronda de eliminación a partir de los cabezas de serie (el 1° primero).
    Rellena con 'byes' (None = pasa directo) hasta potencia de 2; el 1 vs el último, etc."""
    if len(seeds) < 2:
        raise ValueError("hacen falta al menos 2 jugadores")
    size = 1
    while size < len(seeds):
        size *= 2
    slots = list(seeds) + [None] * (size - len(seeds))
    order = [0]
    while len(order) < size:                         # orden clásico de llaves: 1-8, 4-5, 2-7, 3-6
        m = len(order) * 2
        order = [x for o in order for x in (o, m - 1 - o)]
    return [(slots[order[i]], slots[order[i + 1]]) for i in range(0, size, 2)]
