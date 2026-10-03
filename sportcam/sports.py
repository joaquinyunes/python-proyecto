"""Definición de deportes: qué eventos existen y qué gesto (cantidad de dedos) los dispara.

Para agregar un deporte propio: `register_sport(Sport(...))`.
"""
from __future__ import annotations

from dataclasses import dataclass

# Nombres reservados: las métricas de torneo los usan, un evento no puede llamarse igual.
RESERVED_KEYS = frozenset(
    {"wins", "losses", "draws", "matches", "win_rate", "points", "league_points"}
)


@dataclass(frozen=True)
class EventType:
    key: str                # identificador estable, ej. "gol"
    label: str              # texto para mostrar
    code: int | None = None  # dedos que se muestran a la cámara (1-10); None = solo manual
    points: int = 0         # puntos que suma al marcador de su equipo
    clip: bool = False      # si True, se guarda el replay de los últimos segundos


@dataclass(frozen=True)
class Sport:
    key: str
    name: str
    events: tuple[EventType, ...]

    def event(self, key: str) -> EventType:
        for e in self.events:
            if e.key == key:
                return e
        raise KeyError(f"'{key}' no es un evento de {self.key}")

    def by_code(self, code: int) -> EventType | None:
        return next((e for e in self.events if e.code == code), None)


SPORTS: dict[str, Sport] = {}


def register_sport(sport: Sport) -> Sport:
    keys = [e.key for e in sport.events]
    codes = [e.code for e in sport.events if e.code is not None]
    if len(set(keys)) != len(keys):
        raise ValueError("eventos con clave repetida")
    if len(set(codes)) != len(codes):
        raise ValueError("dos eventos usan el mismo gesto")
    clash = RESERVED_KEYS.intersection(keys)
    if clash:
        raise ValueError(f"claves reservadas: {sorted(clash)}")
    SPORTS[sport.key] = sport
    return sport


def get_sport(key: str) -> Sport:
    try:
        return SPORTS[key]
    except KeyError:
        raise KeyError(f"deporte desconocido '{key}'. Disponibles: {sorted(SPORTS)}") from None


register_sport(Sport("futbol", "Fútbol", (
    EventType("gol", "Gol", code=1, points=1, clip=True),
    EventType("asistencia", "Asistencia", code=2),
    EventType("atajada", "Atajada", code=3),
)))

register_sport(Sport("basquet", "Básquet", (
    EventType("libre", "Tiro libre", code=1, points=1),
    EventType("doble", "Doble", code=2, points=2),
    EventType("triple", "Triple", code=3, points=3, clip=True),
    EventType("asistencia", "Asistencia", code=4),
    EventType("rebote", "Rebote", code=5),
    EventType("robo", "Robo", code=6),
    EventType("tapon", "Tapón", code=7),
)))

# Pádel: el marcador real (games/sets) lo lleva la app; acá se cuentan puntos ganados
# y el resultado del partido se indica con finish_match(winner_side=...).
register_sport(Sport("padel", "Pádel", (
    EventType("punto", "Punto ganado", code=1, points=1),
    EventType("winner", "Golpe ganador", code=2, clip=True),
    EventType("saque_directo", "Saque directo", code=3),
    EventType("error_no_forzado", "Error no forzado", code=4),
)))
