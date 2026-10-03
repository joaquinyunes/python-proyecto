import re
import subprocess

import cv2
import numpy as np
import pytest

from sportcam.replay import ReplayBuffer


def _frame(i, w=640, h=360):
    """Frame cuyo color codifica el índice i (para saber qué frame es al leer el mp4)."""
    f = np.zeros((h, w, 3), np.uint8)
    f[:] = (i % 250, (i // 250) % 250, 128)
    cv2.putText(f, str(i), (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
    return f


def _probe(path):
    import imageio_ffmpeg
    out = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(path)],
                         capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    seconds = int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3])
    return seconds, "Video: h264" in out, out


def test_jitter_de_camara_no_pierde_frames():
    rb = ReplayBuffer(seconds=60, fps=25)
    rng = np.random.default_rng(0)
    t, kept = 0.0, 0
    for _ in range(500):                       # cámara de 25 fps con +-6 ms de jitter
        t += 0.04 + rng.uniform(-0.006, 0.006)
        kept += rb.push(_frame(0, 64, 36), t=t)
    assert kept >= 495
    rb.close()


def test_buffer_descarta_lo_viejo_y_limita_fps():
    rb = ReplayBuffer(seconds=10, fps=25)
    for i in range(25 * 30):                  # 30 s simulados a 25 fps
        rb.push(_frame(i, 160, 90), t=i / 25)
    assert 9.5 <= rb.duration <= 10.0
    assert rb.push(_frame(0, 160, 90), t=(25 * 30 - 1) / 25 + 0.005) is False   # llega muy pronto: se descarta
    rb.close()


def test_clip_termina_en_el_ultimo_frame_y_es_h264(tmp_path):
    rb = ReplayBuffer(seconds=20, fps=25)
    for i in range(25 * 30):
        rb.push(_frame(i), t=i / 25)
    path, span = rb.save_clip(tmp_path / "sub" / "gol.mp4", seconds=8).result(timeout=120)
    assert path.exists() and not list(tmp_path.rglob("*.part.mp4"))
    assert 7.8 <= span <= 8.1
    seconds, is_h264, info = _probe(path)
    assert is_h264, info
    assert abs(seconds - span) < 0.5

    cap = cv2.VideoCapture(str(path))
    n, last = 0, None
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        n, last = n + 1, fr
    cap.release()
    assert n >= 190
    # el color codifica el índice: el último frame del clip debe ser (casi) el último que entró
    expected = _frame(25 * 30 - 1)
    assert abs(int(last[200, 300, 0]) - int(expected[200, 300, 0])) <= 6
    rb.close()


def test_clip_pide_mas_de_lo_que_hay_devuelve_lo_disponible(tmp_path):
    rb = ReplayBuffer(seconds=180, fps=25)
    for i in range(25 * 5):
        rb.push(_frame(i, 320, 180), t=i / 25)
    _, span = rb.save_clip(tmp_path / "c.mp4", seconds=180).result(timeout=60)
    assert 4.5 <= span <= 5.0
    rb.close()


def test_buffer_vacio_falla_con_mensaje_claro(tmp_path):
    rb = ReplayBuffer()
    with pytest.raises(RuntimeError, match="suficiente video"):
        rb.save_clip(tmp_path / "x.mp4").result(timeout=10)
    rb.close()


def test_frames_de_medida_impar_se_ajustan(tmp_path):
    rb = ReplayBuffer(seconds=5, fps=25)
    for i in range(50):
        rb.push(_frame(i, 321, 181), t=i / 25)
    path, _ = rb.save_clip(tmp_path / "odd.mp4").result(timeout=60)
    assert path.exists()
    rb.close()
