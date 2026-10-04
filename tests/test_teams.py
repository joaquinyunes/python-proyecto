import pytest

from sportcam.store import Store


@pytest.fixture
def liga():
    db = Store(":memory:")
    ps = {n: db.add_player(n) for n in ["Ana", "Beto", "Cami", "Dani", "Eli", "Fede"]}
    t = db.create_tournament("Copa", "futbol", ps["Ana"], rank_by=("wins", "gol"))
    teams = {
        "Rojos": db.create_team(t, "Rojos", [ps["Ana"], ps["Beto"]]),
        "Azules": db.create_team(t, "Azules", [ps["Cami"], ps["Dani"]]),
        "Verdes": db.create_team(t, "Verdes", [ps["Eli"], ps["Fede"]]),
    }
    yield db, t, ps, teams
    db.close()


def _jugar(db, fixture, goles_a, goles_b):
    """Juega un cruce: goles_a/goles_b = cantidad de goles de un jugador de cada equipo."""
    m = db.start_team_fixture(fixture["id"])
    pa = next(p for p in db.team_players(fixture["a_team"]))
    pb = next(p for p in db.team_players(fixture["b_team"]))
    for _ in range(goles_a):
        db.add_event(m, pa, "gol")
    for _ in range(goles_b):
        db.add_event(m, pb, "gol")
    db.finish_match(m)
    return m


def test_equipos_y_restricciones(liga):
    db, t, ps, teams = liga
    assert [x["name"] for x in db.list_teams(t)] == ["Rojos", "Azules", "Verdes"]
    with pytest.raises(ValueError, match="otro equipo"):
        db.create_team(t, "Otro", [ps["Ana"]])
    with pytest.raises(ValueError, match="ya existe"):
        db.create_team(t, "Rojos", [db.add_player("Nuevo")])
    with pytest.raises(ValueError):
        db.create_team(t, "Vacio", [])
    assert db.schedule_teams(t) == 3
    with pytest.raises(ValueError):
        db.schedule_teams(t)
    with pytest.raises(ValueError, match="calendario"):
        db.create_team(t, "Tarde", [db.add_player("Tarde")])


def test_partido_de_equipos_arma_los_lados(liga):
    db, t, ps, teams = liga
    db.schedule_teams(t)
    f = db.team_fixtures(t)[0]
    m = db.start_team_fixture(f["id"], dorsals={ps["Ana"]: 10})
    lados = {r["player_id"]: r["side"] for r in db._q("SELECT player_id, side FROM match_players WHERE match_id=?", (m,))}
    assert len(lados) == 4 and len({v for v in lados.values()}) == 2
    assert all(lados[p] == "A" for p in db.team_players(f["a_team"]))
    assert all(lados[p] == "B" for p in db.team_players(f["b_team"]))
    with pytest.raises(ValueError):
        db.start_team_fixture(f["id"])
    assert db.team_fixtures(t)[0]["status"] == "en juego"


def test_tabla_de_equipos(liga):
    db, t, ps, teams = liga
    db.schedule_teams(t)
    fx = db.team_fixtures(t)
    assert db.team_standings(t)[0]["played"] == 0
    # Cada cruce lo gana el equipo "a" por 2-0, el primero 1-1
    _jugar(db, fx[0], 1, 1)
    _jugar(db, fx[1], 2, 0)
    _jugar(db, fx[2], 3, 1)
    tabla = db.team_standings(t)
    assert sum(r["played"] for r in tabla) == 6 and sum(r["points"] for r in tabla) == 3 * 2 + 2
    assert [r["position"] for r in tabla] == [1, 2, 3]
    lider = tabla[0]
    assert lider["points"] == max(r["points"] for r in tabla)
    for r in tabla:
        assert r["wins"] + r["draws"] + r["losses"] == r["played"]
    assert sum(r["for"] for r in tabla) == sum(r["against"] for r in tabla) == 8


def test_desempate_por_diferencia(liga):
    db, t, ps, teams = liga
    db.schedule_teams(t)
    fx = db.team_fixtures(t)
    for f in fx:                                  # todos empatan 1-1 salvo uno que gana
        _jugar(db, f, 1, 1)
    assert {r["points"] for r in db.team_standings(t)} == {2}
    assert [r["position"] for r in db.team_standings(t)] == [1, 2, 3]
    # los jugadores también acumulan estadísticas del torneo
    assert db.player_stats(ps["Ana"], tournament_id=t)["matches"] >= 1


def test_pagina_del_torneo_muestra_equipos_escapados(tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from sportcam.viewer import make_handler
    db = Store(":memory:")
    a, b = db.add_player("A"), db.add_player("B")
    t = db.create_tournament("T", "futbol", a)
    db.create_team(t, "<u>Rojos</u>", [a]); db.create_team(t, "Azules", [b])
    db.schedule_teams(t)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/torneo/{t}").read()
        assert b"Equipos" in body and b"&lt;u&gt;Rojos" in body and b"<u>" not in body
    finally:
        srv.shutdown(); db.close()
