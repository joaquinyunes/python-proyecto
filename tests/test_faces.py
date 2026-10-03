import numpy as np
import pytest

pytest.importorskip("dlib")
data = pytest.importorskip("skimage.data")
import cv2

from sportcam.faces import FaceIdentifier


@pytest.fixture(scope="module")
def ident():
    return FaceIdentifier()


@pytest.fixture(scope="module")
def foto():
    """Foto real de dominio público (NASA) que trae scikit-image."""
    return cv2.cvtColor(data.astronaut(), cv2.COLOR_RGB2BGR)


def _cara_principal(ident, img):
    """La cara real de la foto (la de mayor tamaño que aparece arriba)."""
    caras = ident.faces(img)
    assert caras, "no se detectó ninguna cara"
    return min(caras, key=lambda c: c[0][0])    # la más alta de la imagen


def _recorte(img, box, margen=0.8):
    t, r, b, l = box
    h, w = b - t, r - l
    return img[max(0, int(t - margen * h)):int(b + margen * h), max(0, int(l - margen * w)):int(r + margen * w)]


def test_reconoce_a_la_misma_persona_con_otra_escala_y_luz(ident, foto):
    box, _ = _cara_principal(ident, foto)
    vecs = ident.enroll([cv2.resize(_recorte(foto, box), None, fx=1.4, fy=1.4)])   # "registro"
    ident.set_gallery({7: vecs})

    pequena = cv2.resize(foto, None, fx=0.75, fy=0.75)
    oscura = cv2.convertScaleAbs(foto, alpha=0.7, beta=-10)
    for variante in (foto, pequena, oscura):
        box, vec = _cara_principal(ident, variante)
        pid, dist = ident.match(vec)
        assert pid == 7, f"no reconoció (distancia {dist:.3f})"
        assert dist < 0.45


def test_cara_desconocida_devuelve_none(ident, foto):
    rng = np.random.default_rng(1)
    ident.set_gallery({1: [rng.normal(0, 0.1, 128)], 2: [rng.normal(0, 0.1, 128)]})
    _, vec = _cara_principal(ident, foto)
    assert ident.match(vec)[0] is None
    ident.set_gallery({})
    assert ident.match(vec)[0] is None


def test_dos_candidatos_casi_iguales_es_ambiguo(ident, foto):
    _, vec = _cara_principal(ident, foto)
    ident.set_gallery({1: [vec + 0.001], 2: [vec - 0.001]})
    assert ident.match(vec)[0] is None
    ident.set_gallery({1: [vec + 0.001]})
    assert ident.match(vec)[0] == 1


def test_identify_devuelve_cajas_en_coordenadas_del_frame_original(ident, foto):
    # el detector trabaja a 640 px de ancho: con una imagen de 1280 las cajas deben re-escalarse
    grande = cv2.resize(foto, None, fx=2.5, fy=2.5)
    caja_chica, vec = _cara_principal(ident, foto)
    ident.set_gallery({3: [vec]})
    res = ident.identify(grande)
    assert res and any(m.player_id == 3 for m in res)
    caja_grande = next(m.box for m in res if m.player_id == 3)
    assert abs(caja_grande[0] - caja_chica[0] * 2.5) < 40


def test_sin_caras_no_inventa(ident):
    ruido = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    assert ident.faces(ruido) == [] and ident.faces(np.zeros((480, 640, 3), np.uint8)) == []
    with pytest.raises(ValueError):
        ident.enroll([ruido])
