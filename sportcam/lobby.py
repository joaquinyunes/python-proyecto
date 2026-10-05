"""Sección "Quiero jugar": equipos armados que buscan rival y equipos a los que les faltan jugadores.

Un anuncio es un equipo con día, hora y lugar. Según cuántos jugadores tenga:
  - completo  -> sección "armados": busca RIVAL (se puede emparejar con otro equipo armado)
  - incompleto-> sección "incompletos": busca JUGADORES (muestra cuántos faltan)
Cuando alguien se suma y el equipo se completa, pasa solo de una sección a la otra.

Las fechas son hora local del servidor (datetime sin zona o epoch en segundos).
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from .sports import get_sport
from .store import Store

DEFAULT_TEAM_SIZE = {"futbol": 5, "basquet": 5, "padel": 2}   # jugadores por equipo
OVERLAP_S = 2 * 3600       # un jugador no puede estar en dos anuncios del mismo deporte a la vez


def _ts(value: datetime | float) -> float:
    return value.timestamp() if isinstance(value, datetime) else float(value)


class Lobby:
    def __init__(self, store: Store):
        self.s = store

    # ------------------------------------------------------------------ crear
    def create_post(self, creator_id: int, sport: str, starts_at: datetime | float, *,
                    name: str | None = None, team_size: int | None = None, place: str | None = None,
                    note: str | None = None, members: tuple[int, ...] | list[int] = (),
                    now: float | None = None) -> int:
        """Publica un equipo. El creador entra solo; `members` son amigos ya confirmados.
        Sin `team_size` usa el estándar del deporte (fútbol 5, básquet 5, pádel 2)."""
        now = time.time() if now is None else now
        get_sport(sport)
        size = team_size or DEFAULT_TEAM_SIZE.get(sport)
        if size is None:
            raise ValueError(f"indicá team_size para '{sport}'")
        if not 1 <= size <= 30:
            raise ValueError("team_size debe estar entre 1 y 30")
        when = _ts(starts_at)
        if when <= now:
            raise ValueError("el horario tiene que ser a futuro")
        people = [creator_id, *[m for m in members if m != creator_id]]
        if len(set(people)) != len(people) or len(people) > size:
            raise ValueError(f"el equipo es de {size} jugadores como máximo y sin repetir")
        with self.s._lock:
            for pid in people:
                if self.s.get_player(pid) is None:
                    raise ValueError(f"jugador {pid} no existe")
                self._check_free(pid, sport, when)
            post_id = self.s._w(
                "INSERT INTO lobby_posts(sport, name, creator_id, starts_at, team_size, place, note, "
                "created_at) VALUES (?,?,?,?,?,?,?,?)", (sport, name, creator_id, when, size, place, note, now))
            for pid in people:
                self.s._w("INSERT INTO lobby_members(post_id, player_id, joined_at) VALUES (?,?,?)",
                          (post_id, pid, now))
        return post_id

    def _check_free(self, player_id: int, sport: str, when: float, ignore_post: int | None = None) -> None:
        clash = self.s._q(
            "SELECT p.id FROM lobby_posts p JOIN lobby_members m ON m.post_id=p.id "
            "WHERE m.player_id=? AND p.sport=? AND p.status='open' AND ABS(p.starts_at-?)<? AND p.id<>?",
            (player_id, sport, when, OVERLAP_S, ignore_post or -1))
        if clash:
            raise ValueError(f"el jugador {player_id} ya está en otro anuncio de {sport} a esa hora")

    # --------------------------------------------------------- sumarse / salir
    def join_post(self, post_id: int, player_id: int, *, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.s._lock:
            post = self._open_post(post_id, now)
            if self.s.get_player(player_id) is None:
                raise ValueError(f"jugador {player_id} no existe")
            if player_id in post["members"]:
                raise ValueError("ya estás en este equipo")
            if post["missing"] == 0:
                raise ValueError("el equipo ya está completo")
            self._check_free(player_id, post["sport"], post["starts_at"])
            self.s._w("INSERT INTO lobby_members(post_id, player_id, joined_at) VALUES (?,?,?)",
                      (post_id, player_id, now))
        return self.get_post(post_id)

    def leave_post(self, post_id: int, player_id: int, *, now: float | None = None) -> dict | None:
        """Salir del equipo. Si se va el creador, el anfitrión pasa al siguiente en llegar;
        si no queda nadie, el anuncio se cancela (devuelve None)."""
        now = time.time() if now is None else now
        with self.s._lock:
            post = self._open_post(post_id, now)
            if player_id not in post["members"]:
                raise ValueError("no estás en este equipo")
            self.s._w("DELETE FROM lobby_members WHERE post_id=? AND player_id=?", (post_id, player_id))
            rest = [m for m in post["members"] if m != player_id]
            if not rest:
                self.s._w("UPDATE lobby_posts SET status='cancelled' WHERE id=?", (post_id,))
                return None
            if post["creator_id"] == player_id:
                self.s._w("UPDATE lobby_posts SET creator_id=? WHERE id=?", (rest[0], post_id))
        return self.get_post(post_id)

    def cancel_post(self, post_id: int, by_player_id: int, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.s._lock:
            post = self._open_post(post_id, now)
            if post["creator_id"] != by_player_id:
                raise PermissionError("solo quien creó el anuncio puede cancelarlo")
            self.s._w("UPDATE lobby_posts SET status='cancelled' WHERE id=?", (post_id,))

    # ---------------------------------------------------------------- consultar
    def get_post(self, post_id: int) -> dict:
        rows = self.s._q("SELECT * FROM lobby_posts WHERE id=?", (post_id,))
        if not rows:
            raise ValueError(f"anuncio {post_id} no existe")
        post = dict(rows[0])
        post["members"] = [r["player_id"] for r in self.s._q(
            "SELECT player_id FROM lobby_members WHERE post_id=? ORDER BY joined_at, player_id", (post_id,))]
        post["size"] = len(post["members"])
        post["missing"] = post["team_size"] - post["size"]
        post["ready"] = post["missing"] == 0
        return post

    def _open_post(self, post_id: int, now: float) -> dict:
        post = self.get_post(post_id)
        if post["status"] != "open":
            raise ValueError("el anuncio ya no está disponible")
        if post["starts_at"] <= now:
            raise ValueError("el horario del anuncio ya pasó")
        return post

    def list_posts(self, sport: str | None = None, day: date | None = None, *,
                   now: float | None = None) -> dict[str, list[dict]]:
        """Anuncios vigentes, por horario. {"armados": [...], "incompletos": [...]}.
        `day` filtra por fecha (hora local); sin `day` muestra todo lo que viene."""
        now = time.time() if now is None else now
        lo, hi = now, float("inf")
        if day is not None:
            start = datetime.combine(day, datetime.min.time())
            lo, hi = max(now, start.timestamp()), (start + timedelta(days=1)).timestamp()
        sql, params = ("SELECT id FROM lobby_posts WHERE status='open' AND starts_at>? AND starts_at<?", [lo, hi])
        if sport is not None:
            sql += " AND sport=?"; params.append(sport)
        out: dict[str, list[dict]] = {"armados": [], "incompletos": []}
        for r in self.s._q(sql + " ORDER BY starts_at, id", tuple(params)):
            post = self.get_post(r["id"])
            out["armados" if post["ready"] else "incompletos"].append(post)
        return out

    def suggest_rivals(self, post_id: int, *, tolerance_min: float = 60, now: float | None = None) -> list[dict]:
        """Equipos armados del mismo deporte, a horario parecido, que podrían ser rival."""
        now = time.time() if now is None else now
        me = self._open_post(post_id, now)
        return [p for p in self.list_posts(me["sport"], now=now)["armados"]
                if p["id"] != post_id and abs(p["starts_at"] - me["starts_at"]) <= tolerance_min * 60]

    # ----------------------------------------------------------------- emparejar
    def match_posts(self, post_a: int, post_b: int, *, dorsals: dict[int, int] | None = None,
                    tolerance_min: float = 60, now: float | None = None) -> int:
        """Empareja dos equipos armados y crea el partido (A vs B). Devuelve match_id."""
        now = time.time() if now is None else now
        if post_a == post_b:
            raise ValueError("un equipo no puede jugar contra sí mismo")
        with self.s._lock:
            a, b = self._open_post(post_a, now), self._open_post(post_b, now)
            if a["sport"] != b["sport"]:
                raise ValueError("son de deportes distintos")
            if not (a["ready"] and b["ready"]):
                raise ValueError("los dos equipos tienen que estar completos")
            if abs(a["starts_at"] - b["starts_at"]) > tolerance_min * 60:
                raise ValueError("los horarios no coinciden")
            if set(a["members"]) & set(b["members"]):
                raise ValueError("hay jugadores repetidos en los dos equipos")
            dorsals = dorsals or {}
            match_id = self.s.create_match(a["sport"])
            for post, side in ((a, "A"), (b, "B")):
                for pid in post["members"]:
                    self.s.add_match_player(match_id, pid, side, dorsals.get(pid))
            for pid_ in (post_a, post_b):
                self.s._w("UPDATE lobby_posts SET status='matched', match_id=? WHERE id=?", (match_id, pid_))
        return match_id
