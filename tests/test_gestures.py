import itertools

import numpy as np
import pytest

from sportcam.gestures import (NumberAssembler, StableReading, count_fingers, fingers_up,
                               total_fingers, HandReading)

NAMES = ["pulgar", "indice", "medio", "anular", "menique"]
MCP = {"indice": (-0.35, -0.95), "medio": (-0.05, -1.0), "anular": (0.22, -0.95), "menique": (0.45, -0.82)}
BASE = {"indice": 5, "medio": 9, "anular": 13, "menique": 17}


def _rot2(v, deg):
    a = np.radians(deg)
    return np.array([v[0] * np.cos(a) - v[1] * np.sin(a), v[0] * np.sin(a) + v[1] * np.cos(a)])


def make_hand(up: dict[str, bool]) -> np.ndarray:
    """Mano sintética (21 landmarks, palma = 1) apuntando hacia arriba."""
    lm = np.zeros((21, 3))
    lm[0] = (0, 0, 0)
    for name, base in BASE.items():
        mcp = np.array(MCP[name]); d = np.array([0.0, -1.0])
        pip = mcp + 0.45 * d
        if up[name]:
            dip, tip = pip + 0.30 * d, pip + 0.55 * d
        else:                                   # dedo doblado hacia la palma
            dip = pip + 0.30 * _rot2(d, 100 if name != "menique" else -100)
            sgn = 1 if name != "menique" else -1
            tip = dip + 0.25 * _rot2(d, sgn * 165)
        lm[base, :2], lm[base + 1, :2], lm[base + 2, :2], lm[base + 3, :2] = mcp, pip, dip, tip
    lm[1, :2], lm[2, :2] = (-0.25, -0.25), (-0.5, -0.45)
    if up["pulgar"]:
        lm[3, :2], lm[4, :2] = (-0.78, -0.62), (-1.05, -0.76)
    else:                                       # pulgar cruzado sobre la palma
        lm[3, :2], lm[4, :2] = (-0.42, -0.68), (-0.12, -0.6)
    return lm


def random_pose(lm, rng, noise=0.0):
    ang = rng.uniform(0, 2 * np.pi)
    rz = np.array([[np.cos(ang), -np.sin(ang), 0], [np.sin(ang), np.cos(ang), 0], [0, 0, 1]])
    tilt = np.radians(rng.uniform(-25, 25))    # inclinación hacia la cámara
    rx = np.array([[1, 0, 0], [0, np.cos(tilt), -np.sin(tilt)], [0, np.sin(tilt), np.cos(tilt)]])
    out = (lm @ rz.T @ rx.T) * rng.uniform(60, 400) + rng.uniform(0, 500, 3)
    return out + rng.normal(0, noise * 60, out.shape)


COMBOS = list(itertools.product([False, True], repeat=5))


@pytest.mark.parametrize("flags", COMBOS, ids=lambda f: "".join("1" if x else "0" for x in f))
def test_las_32_combinaciones_de_dedos(flags):
    up = dict(zip(NAMES, flags))
    assert fingers_up(make_hand(up)) == up


def test_invariante_a_rotacion_escala_e_inclinacion_con_ruido():
    rng = np.random.default_rng(42)
    wrong = 0
    for flags in COMBOS:
        up = dict(zip(NAMES, flags))
        for _ in range(20):
            lm = random_pose(make_hand(up), rng, noise=0.004)
            wrong += fingers_up(lm) != up
    assert wrong / (len(COMBOS) * 20) < 0.02, f"{wrong} de {len(COMBOS) * 20} mal"


def test_conteo_total():
    assert count_fingers(make_hand(dict.fromkeys(NAMES, False))) == 0
    assert count_fingers(make_hand(dict.fromkeys(NAMES, True))) == 5
    hand = lambda n: HandReading(make_hand(dict.fromkeys(NAMES, True)), n, {}, (0, 0))
    assert total_fingers([hand(5), hand(3)]) == 8
    assert total_fingers([]) is None          # sin manos != puño (0)


# ------------------------------------------------------------------ estabilidad
def feed(sr, seq, fps=30):
    """seq: lista de (valor, segundos). Devuelve [(t, emitido)]."""
    out, t = [], 0.0
    for value, dur in seq:
        for _ in range(int(dur * fps)):
            r = sr.update(value, t)
            if r is not None:
                out.append((round(t, 2), r))
            t += 1 / fps
    return out


def test_emite_una_sola_vez_al_sostener():
    assert [v for _, v in feed(StableReading(), [(3, 3.0)])] == [3]


def test_ignora_gestos_de_paso():
    assert feed(StableReading(), [(None, 1), (2, 0.4), (3, 0.3), (4, 0.3), (None, 1)]) == []


def test_tolera_frames_ruidosos():
    seq = [(3, 0.5), (2, 0.03), (3, 0.5), (4, 0.03), (3, 0.5)]
    assert [v for _, v in feed(StableReading(), seq)] == [3]


def test_mismo_numero_requiere_bajar_la_mano():
    sr = StableReading()
    assert [v for _, v in feed(sr, [(1, 1.2), (1, 1.2)])] == [1]
    sr = StableReading()
    assert [v for _, v in feed(sr, [(1, 1.2), (None, 1.2), (1, 1.2)])] == [1, 1]
    sr = StableReading()
    assert [v for _, v in feed(sr, [(1, 1.2), (2, 1.2), (1, 1.2)])] == [1, 2, 1]


def test_tarda_aprox_hold_en_emitir():
    (t, _), = feed(StableReading(hold_s=0.8), [(5, 2.0)])
    assert 0.7 <= t <= 0.95


# --------------------------------------------------------------------- números
def test_numero_de_dos_cifras():
    na = NumberAssembler(commit_after=1.5)
    na.push(1, 0.0); na.push(0, 1.0)
    assert na.poll(2.0) is None and na.text == "10"
    assert na.poll(2.6) == 10 and na.text == ""


def test_una_cifra_y_tercer_digito_reinicia():
    na = NumberAssembler()
    na.push(7, 0.0)
    assert na.poll(1.6) == 7
    na.push(1, 5); na.push(2, 5.5); na.push(3, 6.0)
    assert na.text == "3"                      # máximo 2 cifras: el tercero arranca otro número


def test_diez_dedos_no_es_una_cifra():
    na = NumberAssembler()
    na.push(10, 0.0)
    assert na.poll(5.0) is None


# ----------------------------------------------------------- MediaPipe (humo)
def test_tracker_carga_el_modelo_y_no_inventa_manos():
    """Verifica la integración con MediaPipe: frames sin manos -> lista vacía, sin errores."""
    try:
        from sportcam.gestures import HandTracker
        tracker = HandTracker()
    except RuntimeError as e:        # sin internet para bajar el modelo
        pytest.skip(str(e))
    try:
        blank = np.zeros((480, 640, 3), np.uint8)
        noise = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
        for i in range(5):
            assert tracker.read(blank, 1.0 + i * 0.033) == []
            assert tracker.read(noise, 1.0 + i * 0.033 + 0.01) == []
        assert tracker.read(blank, 0.0) == []     # timestamp que retrocede no debe romper
    finally:
        tracker.close()
