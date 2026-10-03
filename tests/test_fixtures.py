import itertools

import pytest

from sportcam.fixtures import knockout_first_round, round_robin
from sportcam.store import Store


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8])
def test_round_robin_cada_par_se_enfrenta_una_vez(n):
    rounds = round_robin(list(range(1, n + 1)))
    pairs = [frozenset(p) for r in rounds for p in r]
    assert sorted(map(sorted, pairs)) == sorted(map(sorted, map(set, itertools.combinations(range(1, n + 1), 2))))
    assert len(pairs) == len(set(pairs)) == n * (n - 1) // 2
    for r in rounds:                                   # nadie juega dos veces en la misma ronda
        jugadores = [x for p in r for x in p]
        assert len(jugadores) == len(set(jugadores))
    assert len(rounds) == (n - 1 if n % 2 == 0 else n)


def test_round_robin_ida_y_vuelta_y_errores():
    rounds = round_robin([1, 2, 3, 4], double=True)
    assert len([p for r in rounds for p in r]) == 12
    with pytest.raises(ValueError):
        round_robin([1])
    with pytest.raises(ValueError):
        round_robin([1, 1, 2])


def test_llaves_de_eliminacion():
    assert knockout_first_round([1, 2, 3, 4]) == [(1, 4), (2, 3)]
    r8 = knockout_first_round(list(range(1, 9)))
    assert r8 == [(1, 8), (4, 5), (2, 7), (3, 6)]
    r5 = knockout_first_round([1, 2, 3, 4, 5])           # 3 byes, los mejores sembrados descansan
    assert sorted(x for p in r5 for x in p if x) == [1, 2, 3, 4, 5]
    assert {p[0] for p in r5 if p[1] is None} == {1, 2, 3}
    assert all(p[0] is not None for p in r5)
    with pytest.raises(ValueError):
        knockout_first_round([1])


@pytest.fixture
def db():
    s = Store(":memory:")
    yield s
    s.close()


def test_calendario_en_el_store_y_estados(db):
    a, b, c = (db.add_player(n) for n in "ABC")
    t = db.create_tournament("Liga", "futbol", a, rank_by=("wins", "gol"))
    db.join_tournament(t, b); db.join_tournament(t, c)
    assert db.schedule_tournament(t) == 3
    with pytest.raises(ValueError):
        db.schedule_tournament(t)                       # no se duplica
    fx = db.tournament_fixtures(t)
    assert [f["status"] for f in fx] == ["pendiente"] * 3
    m = db.start_fixture(fx[0]["id"], dorsal_a=10, dorsal_b=7)
    assert db.tournament_fixtures(t)[0]["status"] == "en juego"
    with pytest.raises(ValueError):
        db.start_fixture(fx[0]["id"])                   # ya empezó
    db.add_event(m, db.player_by_dorsal(m, 10), "gol")
    db.finish_match(m)
    assert db.tournament_fixtures(t)[0]["status"] == "jugado"
    assert db.leaderboard(t)[0]["matches"] == 1


def test_resumen_y_mvp(db):
    a, b = db.add_player("Ana"), db.add_player("Beto")
    m = db.create_match("futbol")
    db.add_match_player(m, a, "A", 10); db.add_match_player(m, b, "B", 7)
    assert db.match_summary(m)["mvp"] is None           # sin acciones no hay figura
    db.add_event(m, a, "gol"); db.add_event(m, a, "gol"); db.add_event(m, b, "gol")
    db.add_event(m, b, "atajada"); db.add_event(m, b, "atajada")
    db.finish_match(m)
    s = db.match_summary(m)
    assert s["score"] == {"A": 2, "B": 1} and s["winner"] == "A" and s["mvp"] == a
    ana = next(p for p in s["players"] if p["player_id"] == a)
    assert ana["events"] == {"gol": 2} and ana["points"] == 2
