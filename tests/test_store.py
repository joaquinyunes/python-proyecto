import numpy as np
import pytest

from sportcam.sports import EventType, Sport, register_sport
from sportcam.store import Store


@pytest.fixture
def db():
    s = Store(":memory:")
    yield s
    s.close()


def _players(db, n=4):
    return [db.add_player(f"J{i}", face_consent=True) for i in range(1, n + 1)]


def _play(db, sport, side_a, side_b, goals, tournament_id=None, winner=None):
    """Juega un partido: side_a/side_b = ids; goals = lista (player_id, evento)."""
    m = db.create_match(sport, tournament_id=tournament_id)
    for i, p in enumerate(side_a):
        db.add_match_player(m, p, "A", dorsal=10 + i)
    for i, p in enumerate(side_b):
        db.add_match_player(m, p, "B", dorsal=20 + i)
    for p, ev in goals:
        db.add_event(m, p, ev)
    db.finish_match(m, winner)
    return m


def test_stats_futbol_basicas(db):
    a, b, *_ = _players(db)
    _play(db, "futbol", [a], [b], [(a, "gol"), (a, "gol"), (b, "gol"), (b, "atajada")])
    _play(db, "futbol", [a], [b], [(b, "gol"), (a, "asistencia")])
    sa, sb = db.player_stats(a), db.player_stats(b)
    assert (sa["matches"], sa["wins"], sa["losses"], sa["draws"]) == (2, 1, 1, 0)
    assert sa["events"] == {"gol": 2, "asistencia": 1}
    assert sa["points"] == 2 and sa["win_rate"] == 50.0
    assert sb["events"] == {"gol": 2, "atajada": 1}
    assert sa["league_points"] == 3


def test_empate_por_marcador(db):
    a, b, *_ = _players(db)
    m = _play(db, "futbol", [a], [b], [(a, "gol"), (b, "gol")])
    assert db.get_match(m)["winner"] == "D"
    assert db.player_stats(a)["draws"] == 1 and db.player_stats(a)["league_points"] == 1


def test_basquet_puntos_y_filtro_por_deporte(db):
    a, b, *_ = _players(db)
    _play(db, "basquet", [a], [b], [(a, "triple"), (a, "doble"), (b, "libre")])
    _play(db, "futbol", [a], [b], [(b, "gol")])
    assert db.player_stats(a, sport="basquet")["points"] == 5
    assert db.player_stats(a, sport="futbol")["points"] == 0
    assert db.player_stats(a)["matches"] == 2  # sin filtro cuenta ambos deportes


def test_padel_resultado_explicito(db):
    a, b, c, d = _players(db)
    _play(db, "padel", [a, b], [c, d], [(a, "winner")], winner="B")  # ganó B pese a marcador
    assert db.player_stats(c)["wins"] == 1 and db.player_stats(a)["losses"] == 1


def test_integridad(db):
    a, b, *_ = _players(db)
    m = db.create_match("futbol")
    db.add_match_player(m, a, "A", dorsal=9)
    with pytest.raises(ValueError):          # dorsal repetido
        db.add_match_player(m, b, "B", dorsal=9)
    with pytest.raises(ValueError):          # jugador fuera del partido
        db.add_event(m, b, "gol")
    with pytest.raises(KeyError):            # evento de otro deporte
        db.add_event(m, a, "triple")
    db.finish_match(m)
    with pytest.raises(ValueError):          # partido cerrado
        db.add_event(m, a, "gol")


def test_torneo_gana_por_partidos_ganados_vs_por_goles(db):
    a, b, c, _ = _players(db)
    # A gana 2 partidos 1-0; B pierde uno 0-1 y gana otro 5-0 (más goles, menos victorias)
    results = {}
    for rule in (("wins", "gol"), ("gol", "wins")):
        t = db.create_tournament(f"T{rule[0]}", "futbol", a, rank_by=rule)
        db.join_tournament(t, b); db.join_tournament(t, c)
        _play(db, "futbol", [a], [c], [(a, "gol")], tournament_id=t)
        _play(db, "futbol", [a], [c], [(a, "gol")], tournament_id=t)
        _play(db, "futbol", [b], [c], [(b, "gol")] * 5, tournament_id=t)
        results[rule] = [r["player_id"] for r in db.leaderboard(t)]
    assert results[("wins", "gol")][0] == b or results[("wins", "gol")][0] == a
    # B: 1 victoria, 5 goles; A: 2 victorias, 2 goles
    assert results[("wins", "gol")][:2] == [a, b]
    assert results[("gol", "wins")][:2] == [b, a]


def test_desempate_en_cadena_y_cierre(db):
    a, b, *_ = _players(db)
    t = db.create_tournament("Liga", "futbol", a, rank_by=("wins", "asistencia"))
    db.join_tournament(t, b)
    _play(db, "futbol", [a], [b], [(a, "gol"), (b, "asistencia"), (b, "asistencia")], tournament_id=t)
    _play(db, "futbol", [a], [b], [(b, "gol")], tournament_id=t)
    board = db.leaderboard(t)
    assert [r["wins"] for r in board] == [1, 1]
    assert board[0]["player_id"] == b            # empatan en victorias, B tiene más asistencias
    assert db.close_tournament(t) == b
    s = db.player_stats(b)
    assert s["tournaments_played"] == 1 and s["tournaments_won"] == 1
    assert db.player_stats(a)["tournaments_won"] == 0
    with pytest.raises(ValueError):
        db.join_tournament(t, a)


def test_torneo_valida_reglas_y_miembros(db):
    a, b, c, _ = _players(db)
    with pytest.raises(ValueError):
        db.create_tournament("X", "futbol", a, rank_by=("triple",))  # evento de básquet
    t = db.create_tournament("X", "futbol", a, rank_by=("gol",))
    db.join_tournament(t, b)
    m = db.create_match("futbol", tournament_id=t)
    with pytest.raises(ValueError):                  # c no es miembro
        db.add_match_player(m, c, "A")
    with pytest.raises(ValueError):                  # deporte distinto al del torneo
        db.create_match("padel", tournament_id=t)


def test_caras_requieren_consentimiento_y_se_pueden_borrar(db):
    sin = db.add_player("Sin consentimiento")
    con = db.add_player("Con consentimiento", face_consent=True)
    with pytest.raises(PermissionError):
        db.save_face(sin, np.zeros(128))
    v = np.random.default_rng(0).normal(size=128)
    db.save_face(con, v)
    assert np.allclose(db.load_faces()[con][0], v)
    db.forget_face(con)
    assert db.load_faces() == {} and db.get_player(con)["face_consent"] == 0


def test_deporte_propio(db):
    register_sport(Sport("tenis", "Tenis", (EventType("ace", "Ace", code=1, points=1),)))
    a, b, *_ = _players(db)
    _play(db, "tenis", [a], [b], [(a, "ace")])
    assert db.player_stats(a, sport="tenis")["events"] == {"ace": 1}
    with pytest.raises(ValueError):
        register_sport(Sport("malo", "Malo", (EventType("wins", "x"),)))   # clave reservada
