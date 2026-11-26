"""가짜 picamera2 — 가상 바질 화분을 그려서 사진인 척 돌려준다 (test/fake.sh).

진짜 카메라와 같은 치수(camera/camera.py)로, 가짜 회전판이 돈 각도만큼 돌아간 화분을 그린다.
키는 FAKE_PLANT_CM (기본 9 cm) → 스캔하면 그 키가 나와야 한다.
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "camera"), str(ROOT / "foresee")]
import camera                     # noqa: E402  (치수 · 투영)
from foresee import SHAPES        # noqa: E402  (바질 · 파 모양)

ANGLE = Path(tempfile.gettempdir()) / "greentwin-fake-angle"
SCALE = 2                         # 처리 크기(240×320)의 두 배로 그린다


def pot():
    """화분 옆면 + 흙 윗면의 점들."""
    a, y = np.meshgrid(np.arange(0, 2 * np.pi, 0.02), np.arange(0, camera.RIM_Y, 0.1))
    side = np.stack([camera.POT_R * np.cos(a), y, camera.POT_R * np.sin(a)], -1).reshape(-1, 3)
    r, a = np.meshgrid(np.arange(0, camera.POT_R, 0.1), np.arange(0, 2 * np.pi, 0.02))
    soil = np.stack([r * np.cos(a), np.full_like(r, camera.SOIL_Y), r * np.sin(a)], -1).reshape(-1, 3)
    return side.astype(np.float32), soil.astype(np.float32)


class Picamera2:
    def __init__(self, *a):
        self.cam = int(os.environ.get("PI", "1")) - 1
        self.plant = SHAPES[os.environ.get("FAKE_PLANT", "basil")](float(os.environ.get("FAKE_PLANT_CM", "9")), step=0.08, seed=3)

    def create_still_configuration(self, main=None, **k):
        return main

    def configure(self, cfg):
        pass

    def start(self):
        pass

    def capture_file(self, buf, format="jpeg"):
        deg = float(ANGLE.read_text()) if ANGLE.exists() else 0.0
        w, h = camera.W * SCALE, camera.H * SCALE
        img = np.full((h, w, 3), (205, 220, 235), np.uint8)             # 연한 하늘색 배경판
        side, soil = pot()
        layers = [(side, np.array([185, 100, 60])), (soil, np.array([75, 55, 40])), self.plant]
        for pts, col in layers:
            u, v = camera.project(pts, self.cam, deg)
            iu, iv = np.rint(u * SCALE).astype(int), np.rint(v * SCALE).astype(int)
            for du in (-1, 0, 1):                                       # 점 하나를 3×3 픽셀로 찍는다 (구멍 방지)
                for dv in (-1, 0, 1):
                    ok = (iu + du >= 0) & (iu + du < w) & (iv + dv >= 0) & (iv + dv < h)
                    img[iv[ok] + dv, iu[ok] + du] = col[ok] if col.ndim == 2 else col
        # 진짜 카메라는 세로로 달려 있어서 사진이 누워서 나온다 → 가짜도 눕혀서 돌려준다
        Image.fromarray(img).rotate(int(os.environ.get("ROTATE", "90")), expand=True).save(buf, format="JPEG", quality=90)
