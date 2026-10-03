import numpy as np
import pytest

from sportcam.faces import FaceMatch
from sportcam.gestures import HandReading
from sportcam.replay import ReplayBuffer
from sportcam.session import MatchSession
from sportcam.store import Store

W, H, FPS = 320, 180, 30


class FakeHands:
    """Manos simuladas: `script` = [(dedos o None, segundos), ...]. wrist = posición ESPEJADA."""
    def __init__(self, script, wrist=(160, 90)):
        self.timeline, t = [], 0.0
        for fingers, dur in script:
            self.timeline.append((t, t + dur, fingers))
            t += dur
        self.duration, self.wrist = t, wrist

    def read(self, frame, t):
        for a, b, fingers in self.timeline:
            if a <= t < b and fingers is not None:
                return [HandReading(np.zeros((21, 3)), fingers, {}, self.wrist)]
        return []


class FakeFaces:
    def __init__(self, matches):
        self.matches, self.calls = matches, 0

    def identify(self, frame):
        self.calls += 1
        return self.matches


@pytest.fixture
def setup(tmp_path):
    db = Store(":memory:")
    ana, beto = db.add_player("Ana"), db.add_player("Beto")
    m = db.create_match("futbol")
    db.add_match_player(m, ana, "A", dorsal=10)
    db.add_match_player(m, beto, "B", dorsal=7)
    yield db, m, ana, beto, tmp_path
    db.close()


def run(session, hands, extra=4.0):
    updates = []
    for i in range(int((hands.duration + extra) * FPS)):
        frame = np.full((H, W, 3), i % 255, np.uint8)
        updates += session.process(frame, t=i / FPS)
    return updates


def kinds(updates):
    return [u.kind for u in updates]


def test_gol_por_dorsal_guarda_evento_y_clip(setup):
    db, m, ana, beto, tmp = setup
    hands = FakeHands([(None, 1), (1, 1.2), (None, 1.2), (1, 1.2), (0, 1.2)])   # gol -> "1","0" = 10
    replay = ReplayBuffer(seconds=180, fps=FPS)
    s = MatchSession(db, m, hands=hands, replay=replay, clips_dir=tmp / "clips")
    ups = run(s, hands)
    s.close()
    assert kinds(ups) == ["code", "digit", "digit", "event"], [u.message for u in ups]
    assert db.player_stats(ana)["events"] == {"gol": 1} and db.player_stats(beto)["events"] == {}
    clips = {c["kind"]: c for c in db.list_clips(player_id=ana)}
    assert set(clips) == {"full", "highlight"} and clips["full"]["event_type"] == "gol"
    assert all((tmp / "clips" / c["path"]).exists() for c in clips.values())
    assert clips["full"]["seconds"] > 5 and clips["highlight"]["seconds"] <= 30.5
    assert db.match_score(m) == {"A": 1, "B": 0}
    assert not s.errors


def test_gol_por_cara_no_pide_dorsal(setup):
    db, m, ana, beto, tmp = setup
    hands = FakeHands([(None, 1), (1, 1.5)], wrist=(W - 100, 90))   # espejado -> cara en x=100
    faces = FakeFaces([FaceMatch(beto, 0.3, (60, 140, 120, 60))])   # centro de la cara ~ (100, 90)
    s = MatchSession(db, m, hands=hands, faces=faces, replay=ReplayBuffer(fps=FPS), clips_dir=tmp)
    ups = run(s, hands)
    s.close()
    assert kinds(ups) == ["event"] and "cara" in ups[0].message
    assert db.player_stats(beto)["events"] == {"gol": 1}
    assert len(db.list_clips(player_id=beto, kind="full")) == 1


def test_elige_la_cara_mas_cercana_a_la_mano_con_espejado(setup):
    db, m, ana, beto, tmp = setup
    # Ana a la izquierda (x~60), Beto a la derecha (x~260). La mano, en el frame ESPEJADO,
    # está en x=60  =>  en el frame real está en x=260 => es Beto el que hace el gesto.
    hands = FakeHands([(None, 1), (1, 1.5)], wrist=(60, 90))
    faces = FakeFaces([FaceMatch(ana, .3, (60, 100, 120, 20)), FaceMatch(beto, .3, (60, 300, 120, 220))])
    s = MatchSession(db, m, hands=hands, faces=faces, clips_dir=tmp)
    ups = run(s, hands)
    assert kinds(ups) == ["event"]
    assert db.player_stats(beto)["events"] == {"gol": 1} and db.player_stats(ana)["events"] == {}


def test_cara_lejos_de_la_mano_o_fuera_del_partido_cae_a_dorsal(setup):
    db, m, ana, beto, tmp = setup
    intruso = db.add_player("Intruso")            # reconocido pero no juega este partido
    hands = FakeHands([(None, 1), (1, 1.5)])
    faces = FakeFaces([FaceMatch(intruso, .2, (60, 180, 120, 140))])
    s = MatchSession(db, m, hands=hands, faces=faces, clips_dir=tmp)
    assert kinds(run(s, hands, extra=1.0)) == ["code"]
    assert db.player_stats(intruso)["events"] == {}

    # Ana SÍ juega, pero su cara está muy lejos de la mano que hace el gesto: no se le asigna.
    hands = FakeHands([(None, 1), (1, 1.5)], wrist=(20, 90))      # mano espejada x=20 => real x=300
    faces = FakeFaces([FaceMatch(ana, .2, (70, 40, 110, 0))])     # cara chica en x~20
    s = MatchSession(db, m, hands=hands, faces=faces, clips_dir=tmp)
    assert kinds(run(s, hands, extra=1.0)) == ["code"]
    assert db.player_stats(ana)["events"] == {}


def test_asistencia_no_genera_clip(setup):
    db, m, ana, beto, tmp = setup
    hands = FakeHands([(None, 1), (2, 1.2), (None, 1.2), (7, 1.2)])   # asistencia, dorsal 7
    s = MatchSession(db, m, hands=hands, replay=ReplayBuffer(fps=FPS), clips_dir=tmp)
    ups = run(s, hands)
    s.close()
    assert kinds(ups) == ["code", "digit", "event"]
    assert db.player_stats(beto)["events"] == {"asistencia": 1}
    assert db.list_clips() == []


def test_dorsal_inexistente_se_rechaza(setup):
    db, m, ana, beto, tmp = setup
    hands = FakeHands([(None, 1), (1, 1.2), (None, 1.2), (9, 1.2)])
    s = MatchSession(db, m, hands=hands, clips_dir=tmp)
    assert kinds(run(s, hands)) == ["code", "digit", "rejected"]
    assert db.player_stats(ana)["events"] == {} and db.player_stats(beto)["events"] == {}


def test_timeout_si_nadie_dice_quien(setup):
    db, m, ana, beto, tmp = setup
    hands = FakeHands([(None, 1), (1, 1.2)])
    s = MatchSession(db, m, hands=hands, clips_dir=tmp, dorsal_timeout=5)
    ups = run(s, hands, extra=8.0)
    assert kinds(ups) == ["code", "timeout"]
    assert db.player_stats(ana)["events"] == {}
    assert s.status == "Mostrá un número con los dedos"


def test_gestos_de_paso_y_numeros_sin_accion_no_hacen_nada(setup):
    db, m, ana, beto, tmp = setup
    hands = FakeHands([(None, 1), (1, 0.3), (2, 0.3), (3, 0.3), (None, 1), (0, 3), (8, 1.2)])
    s = MatchSession(db, m, hands=hands, clips_dir=tmp)
    ups = run(s, hands)
    assert "event" not in kinds(ups) and "code" not in kinds(ups)   # 8 dedos: sin acción en fútbol
    assert all(db.player_stats(p)["events"] == {} for p in (ana, beto))


def test_si_falla_el_clip_el_evento_queda_y_se_reporta(setup):
    db, m, ana, beto, tmp = setup
    bloqueado = tmp / "no_es_carpeta"
    bloqueado.write_text("x")                     # mkdir sobre un archivo => falla al exportar
    hands = FakeHands([(None, 1), (1, 1.2), (None, 1.2), (1, 1.2), (0, 1.2)])
    s = MatchSession(db, m, hands=hands, replay=ReplayBuffer(fps=FPS), clips_dir=bloqueado)
    ups = run(s, hands)
    s.close()
    assert "event" in kinds(ups) and db.player_stats(ana)["events"] == {"gol": 1}
    assert db.list_clips() == [] and s.errors and all("clip" in e for e in s.errors)


def test_diez_dedos_deshace_el_ultimo_evento_y_borra_sus_clips(setup):
    db, m, ana, beto, tmp = setup
    hands = FakeHands([(None, 1), (1, 1.2), (None, 1.2), (1, 1.2), (0, 1.2), (None, 4), (10, 1.5)])
    s = MatchSession(db, m, hands=hands, replay=ReplayBuffer(fps=FPS), clips_dir=tmp / "clips")
    ups = []
    for i in range(int((hands.duration + 1) * FPS)):
        ups += s.process(np.full((H, W, 3), i % 255, np.uint8), t=i / FPS)
        if i == int(8.5 * FPS):
            s.replay.close()                      # espera a que se guarden los clips
            assert len(db.list_clips()) == 2
            s.replay = ReplayBuffer(fps=FPS)
    assert kinds(ups)[-2:] == ["event", "undo"], kinds(ups)
    assert db.player_stats(ana)["events"] == {} and db.list_clips() == []
    assert not list((tmp / "clips").rglob("*.mp4"))
    assert s.undo_last().kind == "info"           # ya no queda nada


def test_registro_manual_plan_b(setup):
    db, m, ana, beto, tmp = setup
    s = MatchSession(db, m, hands=FakeHands([]), clips_dir=tmp)
    assert s.record_manual("gol", dorsal=7).kind == "event"
    assert s.record_manual("asistencia", player_id=ana).kind == "event"
    assert s.record_manual("gol", dorsal=99).kind == "rejected"
    assert db.player_stats(beto)["events"] == {"gol": 1}
    with pytest.raises(KeyError):
        s.record_manual("triple", dorsal=7)


def test_check_in_marca_presentes_y_detecta_faltantes(setup):
    db, m, ana, beto, tmp = setup
    intruso = db.add_player("Intruso")
    faces = FakeFaces([FaceMatch(ana, .3, (0, 10, 10, 0)), FaceMatch(intruso, .3, (0, 10, 10, 0)),
                       FaceMatch(None, .9, (0, 10, 10, 0))])
    s = MatchSession(db, m, hands=FakeHands([]), faces=faces, clips_dir=tmp)
    assert s.check_in(np.zeros((H, W, 3), np.uint8)) == [ana]
    assert db.missing_check_in(m) == [beto]
