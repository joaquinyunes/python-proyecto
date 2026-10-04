"""Persistencia en SQLite: jugadores, caras, partidos, eventos, clips y torneos.

Es la implementación de referencia: si ya tenés tu propia base, replicá estos métodos
(los demás módulos solo hablan con esta clase).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

from . import fixtures as fx
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
CREATE TABLE IF NOT EXISTS fixtures(
  id INTEGER PRIMARY KEY, tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
  round INTEGER NOT NULL, a_id INTEGER NOT NULL REFERENCES players(id),
  b_id INTEGER NOT NULL REFERENCES players(id), match_id INTEGER REFERENCES matches(id));
CREATE TABLE IF NOT EXISTS teams(
  id INTEGER PRIMARY KEY, tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
  name TEXT NOT NULL, UNIQUE(tournament_id, name));
CREATE TABLE IF NOT EXISTS team_members(
  tournament_id INTEGER NOT NULL, player_id INTEGER NOT NULL REFERENCES players(id),
  team_id INTEGER NOT NULL REFERENCES teams(id), PRIMARY KEY(tournament_id, player_id));
CREATE TABLE IF NOT EXISTS team_fixtures(
  id INTEGER PRIMARY KEY, tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
  round INTEGER NOT NULL, a_team INTEGER NOT NULL REFERENCES teams(id),
  b_team INTEGER NOT NULL REFERENCES teams(id), match_id INTEGER REFERENCES matches(id));
CREATE TABLE IF NOT EXISTS matches(
  id INTEGER PRIMARY KEY, sport TEXT NOT NULL,
  tournament_id INTEGER REFERENCES tournaments(id),
  started_at REAL NOT NULL, finished_at REAL, winner TEXT CHECK(winner IN ('A','B','D')));
CREATE TABLE IF NOT EXISTS match_players(
  match_id INTEGER NOT NULL REFERENCES matches(id),
  player_id INTEGER NOT NULL REFERENCES players(id),
  side TEXT NOT NULL CHECK(side IN ('A','B')), dorsal INTEGER,
  checked_in INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(match_id, player_id));
CREATE TABLE IF NOT EXISTS match_codes(
  code TEXT PRIMARY KEY, match_id INTEGER NOT NULL REFERENCES matches(id),
  expires_at REAL NOT NULL, max_uses INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL REFERENCES matches(id),
  player_id INTEGER NOT NULL REFERENCES players(id),
  type TEXT NOT NULL, points INTEGER NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  match_id INTEGER NOT NULL, player_id INTEGER NOT NULL,
  path TEXT NOT NULL, seconds REAL NOT NULL, created_at REAL NOT NULL,
  kind TEXT NOT NULL DEFAULT 'full');
"""


class Store:
    def __init__(self, path: str | Path = "sportcam.db"):
        # check_same_thread=False + lock: los clips se registran desde un hilo de fondo.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Bases creadas con versiones anteriores: agrega las columnas nuevas."""
        for table, col, ddl in (
                ("clips", "kind", "TEXT NOT NULL DEFAULT 'full'"),
                ("match_players", "checked_in", "INTEGER NOT NULL DEFAULT 0")):
            cols = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

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
        if side not in ("A", "B"):
            raise ValueError("el equipo debe ser 'A' o 'B'")
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

    # ------------------------------------------------- códigos de partido / check-in
    CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # sin 0/O/1/I/L para no confundir

    def create_match_code(self, match_id: int, *, ttl_hours: float = 4.0,
                          max_uses: int = 22) -> str:
        """Código para que los jugadores se unan al partido (ej. al alquilar la cancha).
        Aleatorio (6 caracteres), vence solo y tiene límite de usos."""
        self._require_open_match(match_id)
        for _ in range(10):
            code = "".join(secrets.choice(self.CODE_ALPHABET) for _ in range(6))
            if not self._q("SELECT 1 FROM match_codes WHERE code=?", (code,)):
                self._w("INSERT INTO match_codes(code, match_id, expires_at, max_uses) "
                        "VALUES (?,?,?,?)", (code, match_id, time.time() + ttl_hours * 3600, max_uses))
                return code
        raise RuntimeError("no se pudo generar un código único")

    def join_with_code(self, code: str, player_id: int, side: str, dorsal: int) -> int:
        """El jugador canjea el código eligiendo equipo y número. Devuelve el match_id.
        Volver a canjear (mismo jugador) cambia su dorsal/equipo sin gastar otro uso.
        Limitá los intentos por usuario en tu app para evitar adivinar códigos."""
        if not 0 <= dorsal <= 99:
            raise ValueError("el dorsal debe estar entre 0 y 99")
        code = code.strip().upper()
        with self._lock:
            rows = self._q("SELECT * FROM match_codes WHERE code=?", (code,))
            if not rows or rows[0]["expires_at"] < time.time():
                raise ValueError("código inválido o vencido")
            c = rows[0]
            if self.get_player(player_id) is None:
                raise ValueError(f"jugador {player_id} no existe")
            already = self.player_in_match(c["match_id"], player_id)
            if not already and c["uses"] >= c["max_uses"]:
                raise ValueError("el código ya alcanzó su cupo de jugadores")
            self.add_match_player(c["match_id"], player_id, side, dorsal)
            if not already:
                self._w("UPDATE match_codes SET uses=uses+1 WHERE code=?", (code,))
            return c["match_id"]

    def set_checked_in(self, match_id: int, player_id: int) -> None:
        self._w("UPDATE match_players SET checked_in=1 WHERE match_id=? AND player_id=?",
                (match_id, player_id))

    def missing_check_in(self, match_id: int) -> list[int]:
        return [r["player_id"] for r in self._q(
            "SELECT player_id FROM match_players WHERE match_id=? AND checked_in=0", (match_id,))]

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
                 seconds: float, kind: str = "full") -> int:
        return self._w("INSERT INTO clips(event_id, match_id, player_id, path, seconds, "
                       "created_at, kind) VALUES (?,?,?,?,?,?,?)",
                       (event_id, match_id, player_id, path, seconds, time.time(), kind))

    def undo_event(self, event_id: int) -> list[str]:
        """Borra un evento mal anotado (y sus clips). Devuelve las rutas de clips para borrar
        los archivos. Solo en partidos abiertos: un partido cerrado no se reescribe."""
        rows = self._q("SELECT match_id FROM events WHERE id=?", (event_id,))
        if not rows:
            raise ValueError(f"evento {event_id} no existe")
        self._require_open_match(rows[0]["match_id"])
        paths = [r["path"] for r in self._q("SELECT path FROM clips WHERE event_id=?", (event_id,))]
        with self._lock, self._db:
            self._db.execute("DELETE FROM clips WHERE event_id=?", (event_id,))
            self._db.execute("DELETE FROM events WHERE id=?", (event_id,))
        return paths

    def event_exists(self, event_id: int) -> bool:
        return bool(self._q("SELECT 1 FROM events WHERE id=?", (event_id,)))

    def last_event(self, match_id: int) -> dict | None:
        rows = self._q("SELECT * FROM events WHERE match_id=? ORDER BY id DESC LIMIT 1", (match_id,))
        return dict(rows[0]) if rows else None

    def expired_clips(self, older_than_days: float) -> list[dict]:
        cutoff = time.time() - older_than_days * 86400
        return [dict(r) for r in self._q("SELECT * FROM clips WHERE created_at < ?", (cutoff,))]

    def delete_clip(self, clip_id: int) -> None:
        self._w("DELETE FROM clips WHERE id=?", (clip_id,))

    def list_clips(self, *, player_id: int | None = None,
                   match_id: int | None = None, kind: str | None = None) -> list[dict]:
        sql = ("SELECT c.*, e.type AS event_type, e.ts AS event_ts, m.sport "
               "FROM clips c JOIN events e ON e.id=c.event_id JOIN matches m ON m.id=c.match_id")
        where, params = [], []
        if player_id is not None:
            where.append("c.player_id=?"); params.append(player_id)
        if match_id is not None:
            where.append("c.match_id=?"); params.append(match_id)
        if kind is not None:
            where.append("c.kind=?"); params.append(kind)
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

    def list_tournaments(self) -> list[dict]:
        return [{**dict(r), "rank_by": tuple(json.loads(r["rank_by"]))}
                for r in self._q("SELECT * FROM tournaments ORDER BY id DESC")]

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

    # ----------------------------------------------------------- calendario
    def schedule_tournament(self, tournament_id: int, *, double: bool = False) -> int:
        """Genera el fixture todos-contra-todos entre los miembros. Devuelve cuántos cruces."""
        t = self.get_tournament(tournament_id)
        if t is None:
            raise ValueError(f"torneo {tournament_id} no existe")
        if t["closed_at"] is not None:
            raise ValueError("el torneo ya está cerrado")
        if self._q("SELECT 1 FROM fixtures WHERE tournament_id=?", (tournament_id,)):
            raise ValueError("el torneo ya tiene calendario")
        members = sorted(r["player_id"] for r in self._q(
            "SELECT player_id FROM tournament_players WHERE tournament_id=?", (tournament_id,)))
        rounds = fx.round_robin(members, double=double)
        with self._lock, self._db:
            for i, pairs in enumerate(rounds, 1):
                for a, b in pairs:
                    self._db.execute("INSERT INTO fixtures(tournament_id, round, a_id, b_id) "
                                     "VALUES (?,?,?,?)", (tournament_id, i, a, b))
        return sum(len(r) for r in rounds)

    def tournament_fixtures(self, tournament_id: int) -> list[dict]:
        """Cruces con su estado: 'pendiente', 'en juego' o 'jugado'."""
        out = []
        for r in self._q(
                "SELECT f.*, m.finished_at, m.winner FROM fixtures f LEFT JOIN matches m "
                "ON m.id=f.match_id WHERE f.tournament_id=? ORDER BY f.round, f.id", (tournament_id,)):
            d = dict(r)
            d["status"] = ("pendiente" if d["match_id"] is None
                           else "jugado" if d["finished_at"] else "en juego")
            out.append(d)
        return out

    def start_fixture(self, fixture_id: int, *, dorsal_a: int | None = None,
                      dorsal_b: int | None = None) -> int:
        """Crea el partido de un cruce (a = lado A, b = lado B) y lo enlaza. Devuelve match_id."""
        rows = self._q("SELECT f.*, t.sport FROM fixtures f JOIN tournaments t "
                       "ON t.id=f.tournament_id WHERE f.id=?", (fixture_id,))
        if not rows:
            raise ValueError(f"cruce {fixture_id} no existe")
        f = rows[0]
        if f["match_id"] is not None:
            raise ValueError("ese cruce ya se está jugando o se jugó")
        m = self.create_match(f["sport"], tournament_id=f["tournament_id"])
        self.add_match_player(m, f["a_id"], "A", dorsal_a)
        self.add_match_player(m, f["b_id"], "B", dorsal_b)
        self._w("UPDATE fixtures SET match_id=? WHERE id=?", (m, fixture_id))
        return m

    # -------------------------------------------------------------- resumen
    def match_summary(self, match_id: int) -> dict:
        """Marcador, eventos por jugador y la figura (MVP) del partido."""
        m = self.get_match(match_id)
        if m is None:
            raise ValueError(f"partido {match_id} no existe")
        players = []
        for r in self._q("SELECT mp.player_id, mp.side, mp.dorsal, p.name FROM match_players mp "
                         "JOIN players p ON p.id=mp.player_id WHERE mp.match_id=?", (match_id,)):
            evs = {e["type"]: e["n"] for e in self._q(
                "SELECT type, COUNT(*) AS n FROM events WHERE match_id=? AND player_id=? GROUP BY type",
                (match_id, r["player_id"]))}
            pts = self._q("SELECT COALESCE(SUM(points),0) AS p FROM events WHERE match_id=? AND "
                          "player_id=?", (match_id, r["player_id"]))[0]["p"]
            players.append({"player_id": r["player_id"], "name": r["name"], "side": r["side"],
                            "dorsal": r["dorsal"], "events": evs, "points": pts,
                            "actions": sum(evs.values())})
        # figura: más puntos; si empatan, más acciones (asistencias, atajadas...); luego id estable
        mvp = max(players, key=lambda x: (x["points"], x["actions"], -x["player_id"]), default=None)
        if mvp is not None and mvp["actions"] == 0:
            mvp = None
        return {"match_id": match_id, "sport": m["sport"], "score": self.match_score(match_id),
                "winner": m["winner"], "finished": m["finished_at"] is not None,
                "players": players, "mvp": mvp["player_id"] if mvp else None}

    # ------------------------------------------------------ torneos por equipos
    def create_team(self, tournament_id: int, name: str, player_ids: list[int]) -> int:
        """Crea un equipo dentro del torneo; los jugadores entran al torneo si no estaban.
        Un jugador solo puede estar en un equipo por torneo."""
        t = self.get_tournament(tournament_id)
        if t is None:
            raise ValueError(f"torneo {tournament_id} no existe")
        if t["closed_at"] is not None:
            raise ValueError("el torneo ya está cerrado")
        if self._q("SELECT 1 FROM team_fixtures WHERE tournament_id=?", (tournament_id,)):
            raise ValueError("el calendario ya fue generado: no se pueden agregar equipos")
        if not player_ids or len(set(player_ids)) != len(player_ids):
            raise ValueError("un equipo necesita jugadores y sin repetir")
        for pid in player_ids:
            if self.get_player(pid) is None:
                raise ValueError(f"jugador {pid} no existe")
            if self._q("SELECT 1 FROM team_members WHERE tournament_id=? AND player_id=?",
                       (tournament_id, pid)):
                raise ValueError(f"el jugador {pid} ya está en otro equipo de este torneo")
        try:
            team_id = self._w("INSERT INTO teams(tournament_id, name) VALUES (?,?)", (tournament_id, name))
        except sqlite3.IntegrityError:
            raise ValueError(f"ya existe un equipo '{name}' en este torneo") from None
        for pid in player_ids:
            self.join_tournament(tournament_id, pid)
            self._w("INSERT INTO team_members(tournament_id, player_id, team_id) VALUES (?,?,?)",
                    (tournament_id, pid, team_id))
        return team_id

    def team_players(self, team_id: int) -> list[int]:
        return [r["player_id"] for r in self._q(
            "SELECT player_id FROM team_members WHERE team_id=? ORDER BY player_id", (team_id,))]

    def list_teams(self, tournament_id: int) -> list[dict]:
        return [{**dict(r), "players": self.team_players(r["id"])} for r in self._q(
            "SELECT * FROM teams WHERE tournament_id=? ORDER BY id", (tournament_id,))]

    def schedule_teams(self, tournament_id: int, *, double: bool = False) -> int:
        """Fixture todos-contra-todos entre los equipos del torneo."""
        if self._q("SELECT 1 FROM team_fixtures WHERE tournament_id=?", (tournament_id,)):
            raise ValueError("el torneo ya tiene calendario de equipos")
        ids = [t["id"] for t in self.list_teams(tournament_id)]
        rounds = fx.round_robin(ids, double=double)
        with self._lock, self._db:
            for i, pairs in enumerate(rounds, 1):
                for a, b in pairs:
                    self._db.execute("INSERT INTO team_fixtures(tournament_id, round, a_team, b_team) "
                                     "VALUES (?,?,?,?)", (tournament_id, i, a, b))
        return sum(len(r) for r in rounds)

    def team_fixtures(self, tournament_id: int) -> list[dict]:
        out = []
        for r in self._q(
                "SELECT f.*, m.finished_at FROM team_fixtures f LEFT JOIN matches m ON m.id=f.match_id "
                "WHERE f.tournament_id=? ORDER BY f.round, f.id", (tournament_id,)):
            d = dict(r)
            d["status"] = ("pendiente" if d["match_id"] is None
                           else "jugado" if d["finished_at"] else "en juego")
            out.append(d)
        return out

    def start_team_fixture(self, fixture_id: int, dorsals: dict[int, int] | None = None) -> int:
        """Crea el partido del cruce: jugadores del equipo A en el lado A y los del B en el B.
        `dorsals` = {player_id: número} (opcional; también se pueden canjear con código)."""
        rows = self._q("SELECT f.*, t.sport FROM team_fixtures f JOIN tournaments t "
                       "ON t.id=f.tournament_id WHERE f.id=?", (fixture_id,))
        if not rows:
            raise ValueError(f"cruce {fixture_id} no existe")
        f = rows[0]
        if f["match_id"] is not None:
            raise ValueError("ese cruce ya se está jugando o se jugó")
        dorsals = dorsals or {}
        m = self.create_match(f["sport"], tournament_id=f["tournament_id"])
        for team, side in ((f["a_team"], "A"), (f["b_team"], "B")):
            for pid in self.team_players(team):
                self.add_match_player(m, pid, side, dorsals.get(pid))
        self._w("UPDATE team_fixtures SET match_id=? WHERE id=?", (m, fixture_id))
        return m

    def team_standings(self, tournament_id: int) -> list[dict]:
        """Tabla de equipos: puntos de tabla (3/1/0), luego diferencia de goles/puntos, luego a favor."""
        table = {t["id"]: {"team_id": t["id"], "name": t["name"], "played": 0, "wins": 0, "draws": 0,
                           "losses": 0, "for": 0, "against": 0} for t in self.list_teams(tournament_id)}
        for f in self.team_fixtures(tournament_id):
            if f["status"] != "jugado":
                continue
            m = self.get_match(f["match_id"])
            score = self.match_score(f["match_id"])
            a, b = table[f["a_team"]], table[f["b_team"]]
            for row, gf, ga in ((a, score["A"], score["B"]), (b, score["B"], score["A"])):
                row["played"] += 1; row["for"] += gf; row["against"] += ga
            if m["winner"] == "D":
                a["draws"] += 1; b["draws"] += 1
            else:
                win, lose = (a, b) if m["winner"] == "A" else (b, a)
                win["wins"] += 1; lose["losses"] += 1
        rows = []
        for r in table.values():
            r["points"] = 3 * r["wins"] + r["draws"]
            r["diff"] = r["for"] - r["against"]
            rows.append(r)
        rows.sort(key=lambda r: (-r["points"], -r["diff"], -r["for"], r["team_id"]))
        return [{"position": i + 1, **r} for i, r in enumerate(rows)]
