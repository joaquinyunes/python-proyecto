import time

import pytest

from sportcam.maintenance import purge_old_clips
from sportcam.store import Store


@pytest.fixture
def db():
    s = Store(":memory:")
    yield s
    s.close()


def _setup(db):
    m = db.create_match("futbol")
    return m, db.add_player("Ana"), db.add_player("Beto")


def test_codigo_flujo_completo(db):
    m, ana, beto = _setup(db)
    code = db.create_match_code(m)
    assert len(code) == 6 and not set(code) & set("01OILl")
    assert db.join_with_code(code.lower(), ana, "A", 10) == m        # minúsculas y espacios ok
    assert db.join_with_code(f" {code} ", beto, "B", 7) == m
    assert db.player_by_dorsal(m, 10) == ana and db.player_by_dorsal(m, 7) == beto
    db.add_event(m, db.player_by_dorsal(m, 10), "gol")                # "gol del 10" -> Ana
    assert db.player_stats(ana)["events"] == {"gol": 1}


def test_codigo_rechaza_invalidos(db):
    m, ana, beto = _setup(db)
    code = db.create_match_code(m)
    with pytest.raises(ValueError, match="inválido"):
        db.join_with_code("ZZZZZZ", ana, "A", 1)
    db.join_with_code(code, ana, "A", 10)
    with pytest.raises(ValueError, match="en uso"):                   # dorsal repetido
        db.join_with_code(code, beto, "B", 10)
    with pytest.raises(ValueError):
        db.join_with_code(code, beto, "B", 100)
    with pytest.raises(ValueError):
        db.join_with_code(code, 999, "B", 5)                          # jugador inexistente
    with pytest.raises(ValueError):
        db.join_with_code(code, beto, "C", 5)                         # equipo inválido


def test_codigo_vence_y_tiene_cupo(db):
    m, ana, beto = _setup(db)
    vencido = db.create_match_code(m, ttl_hours=-1)
    with pytest.raises(ValueError, match="vencido"):
        db.join_with_code(vencido, ana, "A", 1)
    chico = db.create_match_code(m, max_uses=1)
    db.join_with_code(chico, ana, "A", 1)
    db.join_with_code(chico, ana, "A", 2)                             # mismo jugador cambia dorsal: no gasta cupo
    assert db.player_by_dorsal(m, 2) == ana and db.player_by_dorsal(m, 1) is None
    with pytest.raises(ValueError, match="cupo"):
        db.join_with_code(chico, beto, "B", 3)


def test_codigo_no_sirve_con_partido_cerrado(db):
    m, ana, _ = _setup(db)
    code = db.create_match_code(m)
    db.finish_match(m)
    with pytest.raises(ValueError, match="terminó"):
        db.join_with_code(code, ana, "A", 1)
    with pytest.raises(ValueError):
        db.create_match_code(m)


def test_codigos_distintos_por_partido(db):
    m, *_ = _setup(db)
    assert len({db.create_match_code(m) for _ in range(50)}) == 50


def test_deshacer_no_toca_partidos_cerrados(db):
    m, ana, _ = _setup(db)
    db.add_match_player(m, ana, "A", 1)
    e = db.add_event(m, ana, "gol")
    db.finish_match(m)
    with pytest.raises(ValueError):
        db.undo_event(e)


def test_purga_borra_solo_clips_viejos_y_dentro_de_la_carpeta(db, tmp_path):
    m, ana, _ = _setup(db)
    db.add_match_player(m, ana, "A", 1)
    e = db.add_event(m, ana, "gol")
    (tmp_path / "viejo.mp4").write_bytes(b"x"); (tmp_path / "nuevo.mp4").write_bytes(b"x")
    afuera = tmp_path.parent / "afuera.mp4"; afuera.write_bytes(b"x")
    viejo = db.add_clip(e, m, ana, "viejo.mp4", 1)
    db.add_clip(e, m, ana, "nuevo.mp4", 1)
    malo = db.add_clip(e, m, ana, "../afuera.mp4", 1)
    db._w("UPDATE clips SET created_at=? WHERE id IN (?,?)", (time.time() - 40 * 86400, viejo, malo))
    assert purge_old_clips(db, tmp_path, older_than_days=30) == 2
    assert not (tmp_path / "viejo.mp4").exists() and (tmp_path / "nuevo.mp4").exists()
    assert afuera.exists()                                            # no sale de la carpeta
    assert [c["path"] for c in db.list_clips()] == ["nuevo.mp4"]


def test_migracion_de_base_vieja(tmp_path):
    import sqlite3
    path = tmp_path / "vieja.db"
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE players(id INTEGER PRIMARY KEY, name TEXT, created_at REAL, face_consent INTEGER);"
                    "CREATE TABLE match_players(match_id INTEGER, player_id INTEGER, side TEXT, dorsal INTEGER);"
                    "CREATE TABLE clips(id INTEGER PRIMARY KEY, event_id INTEGER, match_id INTEGER,"
                    " player_id INTEGER, path TEXT, seconds REAL, created_at REAL);")
    c.commit(); c.close()
    s = Store(path)
    cols = {r["name"] for r in s._q("PRAGMA table_info(clips)")} | {r["name"] for r in s._q("PRAGMA table_info(match_players)")}
    assert {"kind", "checked_in"} <= cols
    s.close()
