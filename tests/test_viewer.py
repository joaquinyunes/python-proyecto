import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from sportcam.store import Store
from sportcam.viewer import make_handler

VIDEO = bytes(range(256)) * 4      # 1024 bytes "de video"


@pytest.fixture
def server(tmp_path):
    clips = tmp_path / "clips"
    (clips / "partido_1").mkdir(parents=True)
    (clips / "partido_1" / "gol.mp4").write_bytes(VIDEO)
    (tmp_path / "secreto.mp4").write_bytes(b"NO DEBERIA SERVIRSE")
    (clips / "nota.txt").write_text("no es video")

    db = Store(":memory:")
    p = db.add_player("<script>alert(1)</script>")
    q = db.add_player("Beto")
    m = db.create_match("futbol")
    db.add_match_player(m, p, "A", dorsal=9)
    e = db.add_event(m, p, "gol")
    db.add_clip(e, m, p, "partido_1/gol.mp4", 180.0)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, clips))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", p, q
    srv.shutdown()
    db.close()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_nombres_se_escapan_y_aparece_la_jugada(server):
    base, p, q = server
    status, _, body = get(f"{base}/")
    assert status == 200 and b"<script>" not in body and b"&lt;script&gt;" in body
    status, _, body = get(f"{base}/jugador/{p}")
    assert status == 200 and b"<script>alert" not in body
    assert b"src='/clips/partido_1/gol.mp4'" in body and b"Gol" in body
    assert b"Todav" in get(f"{base}/jugador/{q}")[2]       # sin jugadas
    assert get(f"{base}/jugador/999")[0] == 404 and get(f"{base}/nada")[0] == 404


def test_video_completo_y_por_rangos(server):
    base, *_ = server
    status, h, body = get(f"{base}/clips/partido_1/gol.mp4")
    assert status == 200 and body == VIDEO and h["Content-Type"] == "video/mp4"
    status, h, body = get(f"{base}/clips/partido_1/gol.mp4", {"Range": "bytes=10-19"})
    assert status == 206 and body == VIDEO[10:20] and h["Content-Range"] == "bytes 10-19/1024"
    status, h, body = get(f"{base}/clips/partido_1/gol.mp4", {"Range": "bytes=-5"})
    assert status == 206 and body == VIDEO[-5:]
    status, _, body = get(f"{base}/clips/partido_1/gol.mp4", {"Range": "bytes=1000-"})
    assert status == 206 and body == VIDEO[1000:]
    assert get(f"{base}/clips/partido_1/gol.mp4", {"Range": "bytes=5000-"})[0] == 416


@pytest.mark.parametrize("ruta", [
    "/clips/../secreto.mp4", "/clips/%2e%2e/secreto.mp4", "/clips/partido_1/../../secreto.mp4",
    "/clips/nota.txt", "/clips/partido_1/no_existe.mp4", "/clips/%2e%2e%2fsecreto.mp4"])
def test_no_sirve_archivos_fuera_de_clips(server, ruta):
    base, *_ = server
    status, _, body = get(base + ruta)
    assert status == 404 and b"NO DEBERIA" not in body


def test_paginas_de_torneo_y_partido(tmp_path):
    db = Store(":memory:")
    a = db.add_player("<b>Ana</b>"); b = db.add_player("Beto")
    t = db.create_tournament("<i>Liga</i>", "futbol", a, rank_by=("wins", "gol"))
    db.join_tournament(t, b); db.schedule_tournament(t)
    m = db.start_fixture(db.tournament_fixtures(t)[0]["id"], dorsal_a=10, dorsal_b=7)
    db.add_event(m, a, "gol"); db.finish_match(m); db.close_tournament(t)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        _, _, body = get(f"{base}/")
        assert b"/torneo/%d" % t in body and b"<i>" not in body
        status, _, body = get(f"{base}/torneo/{t}")
        assert status == 200 and b"<i>Liga" not in body and b"<b>Ana" not in body
        assert b"Campe" in body and b"jugado" in body and b"/partido/%d" % m in body
        status, _, body = get(f"{base}/partido/{m}")
        assert status == 200 and b"1 - 0" in body and b"&#11088;" in body and b"Terminado" in body
        assert get(f"{base}/torneo/99")[0] == 404 and get(f"{base}/partido/99")[0] == 404
    finally:
        srv.shutdown(); db.close()


def test_pagina_quiero_jugar(tmp_path):
    from datetime import datetime, timedelta
    from sportcam.lobby import Lobby
    db = Store(":memory:")
    ids = [db.add_player(n) for n in ("<b>Ana</b>", "Beto", "Cami", "Dani")]
    lb = Lobby(db)
    manana = datetime.now() + timedelta(days=1)
    lb.create_post(ids[0], "padel", manana, members=[ids[1]], place="Cancha <1>", name="Los <i>Cracks</i>")
    lb.create_post(ids[2], "futbol", manana, note="traer pechera")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        status, _, body = get(f"{base}/quiero-jugar")
        assert status == 200 and b"armados que buscan rival (1)" in body
        assert b"faltan 4" in body and b"2/2" in body and b"1/5" in body
        assert b"<i>Cracks" not in body and b"&lt;i&gt;Cracks" in body and b"Cancha &lt;1&gt;" in body
        status, _, body = get(f"{base}/quiero-jugar?deporte=padel")
        assert b"(1)" in body and b"traer pechera" not in body and b"selected" in body
        dia = manana.date().isoformat()
        assert b"traer pechera" in get(f"{base}/quiero-jugar?dia={dia}")[2]
        assert b"traer pechera" not in get(f"{base}/quiero-jugar?dia=2001-01-01")[2]
        assert get(f"{base}/quiero-jugar?dia=basura&deporte=nada")[0] == 200      # filtros inválidos se ignoran
        assert b"/quiero-jugar" in get(f"{base}/")[2]
    finally:
        srv.shutdown(); db.close()
