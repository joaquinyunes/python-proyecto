"""Persistencia en SQLite: jugadores, caras, partidos, eventos, clips y torneos.

Es la implementación de referencia: si ya tenés tu propia base, replicá estos métodos
(los demás módulos solo hablan con esta clase).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

from . import tournaments
from .sports import get_sport

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS players(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL,
  face_consent INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS faces(
  id INTEGER PRIMARY KEY,
  player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS tournaments(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, sport TEXT NOT NULL,
  owner_id INTEGER NOT NULL REFERENCES players(id), rank_by TEXT NOT NULL,
  created_at REAL NOT NULL, closed_at REAL, champion_id INTEGER REFERENCES players(id));
CREATE TABLE IF NOT EXISTS tournament_players(
  tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
  player_id INTEGER NOT NULL REFERENCES players(id),
  PRIMARY KEY(tournament_id, player_id));
CREATE TABLE IF NOT EXISTS matches(
  id INTEGER PRIMARY KEY, sport TEXT NOT NULL,
  tournament_id INTEGER REFERENCES tournaments(id),
  started_at REAL NOT NULL, finished_at REAL, winner TEXT CHECK(winner IN ('A','B','D')));
CREATE TABLE IF NOT EXISTS match_players(
  match_id INTEGER NOT NULL REFERENCES matches(id),
  player_id INTEGER NOT NULL REFERENCES players(id),
  side TEXT NOT NULL CHECK(side IN ('A','B')), dorsal INTEGER,
  PRIMARY KEY(match_id, player_id));
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL REFERENCES matches(id),
  player_id INTEGER NOT NULL REFERENCES players(id),
  type TEXT NOT NULL, points INTEGER NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  match_id INTEGER NOT NULL, player_id INTEGER NOT NULL,
  path TEXT NOT NULL, seconds REAL NOT NULL, created_at REAL NOT NULL);
"""


class Store:
    def __init__(self, path: str | Path = "sportcam.db"):
        # check_same_thread=False + lock: los clips se registran desde un hilo de fondo.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _w(self, sql: str, params: tuple = ()) -> int:
        with self._lock, self._db:
            return self._db.execute(sql, params).lastrowid

    # ---------------------------------------------------------------- jugadores
    def add_player(self, name: str, *, face_consent: bool = False) -> int:
        return self._w("INSERT INTO players(name, created_at, face_consent) VALUES (?,?,?)",
                       (name, time.time(), int(face_consent)))

    def get_player(self, player_id: int) -> dict | None:
        rows = self._q("SELECT * FROM players WHERE id=?", (player_id,))
        return dict(rows[0]) if rows else None

    def find_player(self, name: str) -> int | None:
        rows = self._q("SELECT id FROM players WHERE name=? ORDER BY id LIMIT 1", (name,))
        return rows[0]["id"] if rows else None

    def list_players(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM players ORDER BY id")]

    # -------------------------------------------------------------------- caras
    def save_face(self, player_id: int, vec: np.ndarray) -> None:
        """Guarda SOLO el vector numérico de la cara (no la foto). Exige consentimiento."""
        player = self.get_player(player_id)
        if player is None:
            raise ValueError(f"jugador {player_id} no existe")
        if not player["face_consent"]:
            raise PermissionError("el jugador no dio consentimiento para el reconocimiento facial")
        self._w("INSERT INTO faces(player_id, vec) VALUES (?,?)",
                (player_id, np.asarray(vec, dtype=np.float64).tobytes()))

    def load_faces(self) -> dict[int, list[np.ndarray]]:
        out: dict[int, list[np.ndarray]] = {}
        for r in self._q("SELECT player_id, vec FROM faces"):
            out.setdefault(r["player_id"], []).append(np.frombuffer(r["vec"], dtype=np.float64))
        return out

    def forget_face(self, player_id: int) -> None:
        """Derecho a borrar: elimina los vectores faciales y revoca el consentimiento."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM faces WHERE player_id=?", (player_id,))
            self._db.execute("UPDATE players SET face_consent=0 WHERE id=?", (player_id,))

    # ----------------------------------------------------------------- partidos
    def create_match(self, sport: str, *, tournament_id: int | None = None) -> int:
        get_sport(sport)
        if tournament_id is not None:
            t = self.get_tournament(tournament_id)
            if t is None:
                raise ValueError(f"torneo {tournament_id} no existe")
            if t["sport"] != sport:
                raise ValueError(f"el torneo es de {t['sport']}, no de {sport}")
            if t["closed_at"] is not None:
                raise ValueError("el torneo ya está cerrado")
        return self._w("INSERT INTO matches(sport, tournament_id, started_at) VALUES (?,?,?)",
                       (sport, tournament_id, time.time()))

    def get_match(self, match_id: int) -> dict | None:
        rows = self._q("SELECT * FROM matches WHERE id=?", (match_id,))
        return dict(rows[0]) if rows else None

    def add_match_player(self, match_id: int, player_id: int, side: str,
                         dorsal: int | None = None) -> None:
        m = self._require_open_match(match_id)
        if m["tournament_id"] is not None and not self._q(
                "SELECT 1 FROM tournament_players WHERE tournament_id=? AND player_id=?",
                (m["tournament_id"], player_id)):
            raise ValueError("el jugador no pertenece al torneo de este partido")
        if dorsal is not None and self._q(
                "SELECT 1 FROM match_players WHERE match_id=? AND dorsal=? AND player_id<>?",
                (match_id, dorsal, player_id)):
            raise ValueError(f"el dorsal {dorsal} ya está en uso en este partido")
        self._w("INSERT OR REPLACE INTO match_players(match_id, player_id, side, dorsal) "
                "VALUES (?,?,?,?)", (match_id, player_id, side, dorsal))

    def player_in_match(self, match_id: int, player_id: int) -> bool:
        return bool(self._q("SELECT 1 FROM match_players WHERE match_id=? AND player_id=?",
                            (match_id, player_id)))

    def player_by_dorsal(self, match_id: int, dorsal: int) -> int | None:
        rows = self._q("SELECT player_id FROM match_players WHERE match_id=? AND dorsal=?",
                       (match_id, dorsal))
        return rows[0]["player_id"] if rows else None

    def match_score(self, match_id: int) -> dict[str, int]:
        score = {"A": 0, "B": 0}
        for r in self._q(
                "SELECT mp.side, COALESCE(SUM(e.points),0) AS pts FROM events e "
                "JOIN match_players mp ON mp.match_id=e.match_id AND mp.player_id=e.player_id "
                "WHERE e.match_id=? GROUP BY mp.side", (match_id,)):
            score[r["side"]] = r["pts"]
        return score

    def finish_match(self, match_id: int, winner_side: str | None = None) -> str:
        """Cierra el partido. Devuelve 'A', 'B' o 'D' (empate).

        Sin `winner_side` se decide por el marcador (suma de puntos de los eventos).
        """
        self._require_open_match(match_id)
        if winner_side is None:
            s = self.match_score(match_id)
            winner_side = "A" if s["A"] > s["B"] else "B" if s["B"] > s["A"] else "D"
        if winner_side not in ("A", "B", "D"):
            raise ValueError("winner_side debe ser 'A', 'B' o 'D'")
        self._w("UPDATE matches SET finished_at=?, winner=? WHERE id=?",
                (time.time(), winner_side, match_id))
        return winner_side

    def _require_open_match(self, match_id: int) -> dict:
        m = self.get_match(match_id)
        if m is None:
            raise ValueError(f"partido {match_id} no existe")
        if m["finished_at"] is not None:
            raise ValueError(f"el partido {match_id} ya terminó")
        return m

    # ------------------------------------------------------------------ eventos
    def add_event(self, match_id: int, player_id: int, event_type: str,
                  ts: float | None = None) -> int:
        m = self._require_open_match(match_id)
        if not self.player_in_match(match_id, player_id):
            raise ValueError(f"el jugador {player_id} no está en el partido {match_id}")
        et = get_sport(m["sport"]).event(event_type)
        return self._w("INSERT INTO events(match_id, player_id, type, points, ts) "
                       "VALUES (?,?,?,?,?)",
                       (match_id, player_id, et.key, et.points, ts or time.time()))

    def add_clip(self, event_id: int, match_id: int, player_id: int, path: str,
                 seconds: float) -> int:
        return self._w("INSERT INTO clips(event_id, match_id, player_id, path, seconds, "
                       "created_at) VALUES (?,?,?,?,?,?)",
                       (event_id, match_id, player_id, path, seconds, time.time()))

    def list_clips(self, *, player_id: int | None = None,
                   match_id: int | None = None) -> list[dict]:
        sql = ("SELECT c.*, e.type AS event_type, e.ts AS event_ts, m.sport "
               "FROM clips c JOIN events e ON e.id=c.event_id JOIN matches m ON m.id=c.match_id")
        where, params = [], []
        if player_id is not None:
            where.append("c.player_id=?"); params.append(player_id)
        if match_id is not None:
            where.append("c.match_id=?"); params.append(match_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [dict(r) for r in self._q(sql + " ORDER BY c.created_at DESC", tuple(params))]

    # ------------------------------------------------------------- estadísticas
    def player_stats(self, player_id: int, *, sport: str | None = None,
                     tournament_id: int | None = None) -> dict:
        """Estadísticas de un jugador, opcionalmente filtradas por deporte y/o torneo."""
        flt, params = "", []
        if sport is not None:
            flt += " AND m.sport=?"; params.append(sport)
        if tournament_id is not None:
            flt += " AND m.tournament_id=?"; params.append(tournament_id)

        events, points = {}, 0
        for r in self._q(
                "SELECT e.type, COUNT(*) AS n, SUM(e.points) AS pts FROM events e "
                "JOIN matches m ON m.id=e.match_id WHERE e.player_id=?" + flt + " GROUP BY e.type",
                (player_id, *params)):
            events[r["type"]] = r["n"]
            points += r["pts"]

        wins = losses = draws = 0
        for r in self._q(
                "SELECT mp.side, m.winner FROM match_players mp JOIN matches m ON m.id=mp.match_id "
                "WHERE mp.player_id=? AND m.finished_at IS NOT NULL" + flt, (player_id, *params)):
            if r["winner"] == "D":
                draws += 1
            elif r["winner"] == r["side"]:
                wins += 1
            else:
                losses += 1
        played = wins + losses + draws

        tflt, tparams = "", []
        if sport is not None:
            tflt += " AND t.sport=?"; tparams.append(sport)
        if tournament_id is not None:
            tflt += " AND t.id=?"; tparams.append(tournament_id)
        t_played = self._q(
            "SELECT COUNT(*) AS n FROM tournament_players tp JOIN tournaments t "
            "ON t.id=tp.tournament_id WHERE tp.player_id=?" + tflt, (player_id, *tparams))[0]["n"]
        t_won = self._q(
            "SELECT COUNT(*) AS n FROM tournaments t WHERE t.champion_id=?" + tflt,
            (player_id, *tparams))[0]["n"]

        return {
            "matches": played, "wins": wins, "losses": losses, "draws": draws,
            "win_rate": round(100 * wins / played, 1) if played else 0.0,
            "points": points, "league_points": 3 * wins + draws,
            "events": events,
            "tournaments_played": t_played, "tournaments_won": t_won,
        }

    # ------------------------------------------------------------------ torneos
    def create_tournament(self, name: str, sport: str, owner_id: int,
                          rank_by: tuple[str, ...] | list[str] = ("wins",)) -> int:
        """Crea un torneo; el creador entra automáticamente. `rank_by` = criterios en orden."""
        rank_by = tournaments.validate_rank_by(get_sport(sport), rank_by)
        if self.get_player(owner_id) is None:
            raise ValueError(f"jugador {owner_id} no existe")
        tid = self._w("INSERT INTO tournaments(name, sport, owner_id, rank_by, created_at) "
                      "VALUES (?,?,?,?,?)", (name, sport, owner_id, json.dumps(rank_by), time.time()))
        self.join_tournament(tid, owner_id)
        return tid

    def get_tournament(self, tournament_id: int) -> dict | None:
        rows = self._q("SELECT * FROM tournaments WHERE id=?", (tournament_id,))
        if not rows:
            return None
        t = dict(rows[0])
        t["rank_by"] = tuple(json.loads(t["rank_by"]))
        return t

    def join_tournament(self, tournament_id: int, player_id: int) -> None:
        t = self.get_tournament(tournament_id)
        if t is None:
            raise ValueError(f"torneo {tournament_id} no existe")
        if t["closed_at"] is not None:
            raise ValueError("el torneo ya está cerrado")
        if self.get_player(player_id) is None:
            raise ValueError(f"jugador {player_id} no existe")
        self._w("INSERT OR IGNORE INTO tournament_players(tournament_id, player_id) VALUES (?,?)",
                (tournament_id, player_id))

    def leaderboard(self, tournament_id: int) -> list[dict]:
        t = self.get_tournament(tournament_id)
        if t is None:
            raise ValueError(f"torneo {tournament_id} no existe")
        members = [r["player_id"] for r in self._q(
            "SELECT player_id FROM tournament_players WHERE tournament_id=?", (tournament_id,))]
        stats = {pid: self.player_stats(pid, tournament_id=tournament_id) for pid in members}
        return tournaments.rank(stats, t["rank_by"])

    def close_tournament(self, tournament_id: int) -> int | None:
        """Cierra el torneo y fija al campeón (primero de la tabla). None si nadie jugó."""
        t = self.get_tournament(tournament_id)
        if t is None:
            raise ValueError(f"torneo {tournament_id} no existe")
        if t["closed_at"] is not None:
            raise ValueError("el torneo ya está cerrado")
        board = self.leaderboard(tournament_id)
        champion = board[0]["player_id"] if board and board[0]["matches"] > 0 else None
        self._w("UPDATE tournaments SET closed_at=?, champion_id=? WHERE id=?",
                (time.time(), champion, tournament_id))
        return champion
