from datetime import date, datetime, timedelta

import pytest

from sportcam.lobby import Lobby
from sportcam.store import Store

NOW = datetime(2026, 10, 5, 12, 0).timestamp()
HOY_20 = datetime(2026, 10, 5, 20, 0)
MANANA_19 = datetime(2026, 10, 6, 19, 0)


@pytest.fixture
def lobby():
    db = Store(":memory:")
    lb = Lobby(db)
    lb.p = {n: db.add_player(n) for n in "ABCDEFGHIJKLMNOP"}
    yield lb
    db.close()


def test_tamanos_por_deporte_y_secciones(lobby):
    p = lobby.p
    # Pádel: pareja completa (armada) y pareja a la que le falta uno
    armada = lobby.create_post(p["A"], "padel", HOY_20, members=[p["B"]], place="Cancha 2", now=NOW)
    falta = lobby.create_post(p["C"], "padel", HOY_20, now=NOW)
    # Fútbol 5: 3 de 5; básquet 5: 5 de 5
    f5 = lobby.create_post(p["D"], "futbol", MANANA_19, members=[p["E"], p["F"]], name="Los Pibes", now=NOW)
    bk = lobby.create_post(p["G"], "basquet", HOY_20, members=[p["H"], p["I"], p["J"], p["K"]], now=NOW)
    out = lobby.list_posts(now=NOW)
    assert [x["id"] for x in out["armados"]] == [armada, bk]
    assert [x["id"] for x in out["incompletos"]] == [falta, f5]
    fut = out["incompletos"][1]
    assert (fut["size"], fut["team_size"], fut["missing"], fut["name"]) == (3, 5, 2, "Los Pibes")
    assert out["armados"][0]["place"] == "Cancha 2"
    assert [x["id"] for x in lobby.list_posts("padel", now=NOW)["armados"]] == [armada]


def test_al_completarse_pasa_a_armados(lobby):
    p = lobby.p
    post = lobby.create_post(p["A"], "padel", HOY_20, now=NOW)
    assert lobby.list_posts(now=NOW)["armados"] == []
    assert lobby.join_post(post, p["B"], now=NOW)["ready"] is True
    out = lobby.list_posts(now=NOW)
    assert [x["id"] for x in out["armados"]] == [post] and out["incompletos"] == []
    with pytest.raises(ValueError, match="completo"):
        lobby.join_post(post, p["C"], now=NOW)


def test_filtro_por_dia_y_los_pasados_no_aparecen(lobby):
    p = lobby.p
    hoy = lobby.create_post(p["A"], "futbol", HOY_20, now=NOW)
    man = lobby.create_post(p["B"], "futbol", MANANA_19, now=NOW)
    assert [x["id"] for x in lobby.list_posts(day=date(2026, 10, 5), now=NOW)["incompletos"]] == [hoy]
    assert [x["id"] for x in lobby.list_posts(day=date(2026, 10, 6), now=NOW)["incompletos"]] == [man]
    assert lobby.list_posts(day=date(2026, 10, 7), now=NOW) == {"armados": [], "incompletos": []}
    despues = (HOY_20 + timedelta(minutes=1)).timestamp()      # ya empezó el de hoy
    assert [x["id"] for x in lobby.list_posts(now=despues)["incompletos"]] == [man]
    with pytest.raises(ValueError, match="pasó"):
        lobby.join_post(hoy, p["C"], now=despues)


def test_validaciones_de_creacion(lobby):
    p = lobby.p
    with pytest.raises(ValueError, match="futuro"):
        lobby.create_post(p["A"], "futbol", datetime(2026, 10, 5, 11, 0), now=NOW)
    with pytest.raises(ValueError, match="máximo"):
        lobby.create_post(p["A"], "padel", HOY_20, members=[p["B"], p["C"]], now=NOW)
    with pytest.raises(KeyError):
        lobby.create_post(p["A"], "ajedrez", HOY_20, now=NOW)
    with pytest.raises(ValueError, match="team_size"):
        from sportcam.sports import EventType, Sport, register_sport
        register_sport(Sport("voley", "Vóley", (EventType("punto", "Punto", points=1),)))
        lobby.create_post(p["A"], "voley", HOY_20, now=NOW)
    assert lobby.create_post(p["A"], "voley", HOY_20, team_size=6, now=NOW)


def test_un_jugador_no_puede_estar_en_dos_anuncios_a_la_vez(lobby):
    p = lobby.p
    lobby.create_post(p["A"], "futbol", HOY_20, now=NOW)
    with pytest.raises(ValueError, match="otro anuncio"):
        lobby.create_post(p["A"], "futbol", HOY_20 + timedelta(hours=1), now=NOW)
    lobby.create_post(p["A"], "padel", HOY_20, now=NOW)                          # otro deporte: ok
    lobby.create_post(p["A"], "futbol", HOY_20 + timedelta(hours=3), now=NOW)    # horario distinto: ok
    otro = lobby.create_post(p["B"], "futbol", HOY_20, now=NOW)
    with pytest.raises(ValueError, match="otro anuncio"):
        lobby.join_post(otro, p["A"], now=NOW)


def test_salir_traspasa_anfitrion_y_cancela_si_queda_vacio(lobby):
    p = lobby.p
    post = lobby.create_post(p["A"], "padel", HOY_20, members=[p["B"]], now=NOW)
    after = lobby.leave_post(post, p["A"], now=NOW)
    assert after["creator_id"] == p["B"] and after["missing"] == 1
    with pytest.raises(ValueError, match="no estás"):
        lobby.leave_post(post, p["A"], now=NOW)
    assert lobby.leave_post(post, p["B"], now=NOW) is None
    assert lobby.list_posts(now=NOW) == {"armados": [], "incompletos": []}
    p2 = lobby.create_post(p["C"], "padel", HOY_20, now=NOW)
    with pytest.raises(PermissionError):
        lobby.cancel_post(p2, p["D"], now=NOW)
    lobby.cancel_post(p2, p["C"], now=NOW)
    assert lobby.list_posts(now=NOW)["incompletos"] == []


def test_emparejar_equipos_crea_el_partido(lobby):
    p = lobby.p
    a = lobby.create_post(p["A"], "padel", HOY_20, members=[p["B"]], now=NOW)
    b = lobby.create_post(p["C"], "padel", HOY_20 + timedelta(minutes=30), members=[p["D"]], now=NOW)
    otro_dep = lobby.create_post(p["E"], "futbol", HOY_20, members=[p["F"], p["G"], p["H"], p["I"]], now=NOW)
    lejano = lobby.create_post(p["J"], "padel", MANANA_19, members=[p["K"]], now=NOW)
    assert [x["id"] for x in lobby.suggest_rivals(a, now=NOW)] == [b]
    for malo, msg in ((otro_dep, "distintos"), (lejano, "horarios")):
        with pytest.raises(ValueError, match=msg):
            lobby.match_posts(a, malo, now=NOW)
    with pytest.raises(ValueError):
        lobby.match_posts(a, a, now=NOW)
    m = lobby.match_posts(a, b, dorsals={p["A"]: 10, p["C"]: 7}, now=NOW)
    db = lobby.s
    lados = {r["player_id"]: r["side"] for r in db._q("SELECT player_id, side FROM match_players WHERE match_id=?", (m,))}
    assert lados == {p["A"]: "A", p["B"]: "A", p["C"]: "B", p["D"]: "B"}
    assert db.player_by_dorsal(m, 10) == p["A"] and db.get_match(m)["sport"] == "padel"
    assert [x["id"] for x in lobby.list_posts("padel", now=NOW)["armados"]] == [lejano]   # los emparejados salen
    assert lobby.get_post(a)["match_id"] == m == lobby.get_post(b)["match_id"]
    with pytest.raises(ValueError, match="disponible"):
        lobby.match_posts(a, lejano, now=NOW)


def test_no_se_empareja_un_equipo_incompleto(lobby):
    p = lobby.p
    a = lobby.create_post(p["A"], "padel", HOY_20, members=[p["B"]], now=NOW)
    b = lobby.create_post(p["C"], "padel", HOY_20, now=NOW)
    with pytest.raises(ValueError, match="completos"):
        lobby.match_posts(a, b, now=NOW)
