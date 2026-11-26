#!/usr/bin/env python3
"""스캔: 회전판 12각도 × 카메라 2대 = 24장 → 사진마다 식물만 남기고 → 3D 로 깎아 → cloud.ply · 키 · 폭.
(Pi ② 가 꺼져 있으면 아래 카메라 한 대의 12장으로 하고, 그랬다고 notes 에 적는다.)

  camera.sh                지금 스캔 (약 1분)
  camera.sh <스캔 폴더>     이미 찍은 사진으로 3D 만 다시 (GREEN · ROTATE 를 바꿔 볼 때)

3D 는 '실루엣 교차'다: 공간을 0.25 cm 칸으로 채우고, 24장 어디서 봐도 식물 안에 떨어지는 칸만 남긴다.
카메라는 고정이고 화분만 도니까, 사진마다 카메라가 어디 있었는지를 회전 각도만으로 안다.
카메라가 몇 mm 비뚤게 달려 있어도 되게, 스캔할 때마다 사진에서 어긋남을 찾아 보정한다 (align).
"""
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

import numpy as np
from PIL import Image

E = os.environ.get
PI1, PI2 = E("PI1", "http://localhost:8765"), E("PI2", "http://localhost:8766")
DATA = Path(E("DATA", "data")).resolve()
SOWN = E("SOWN", "")
ROTATE = int(E("ROTATE", "90"))     # 카메라를 세로로 달았다: 사진을 시계 방향으로 이만큼 돌려 세운다
GREEN = float(E("GREEN", "0.05"))   # 식물로 보는 '초록 정도'. 배경이 섞이면 올리고, 잎이 빠지면 내린다

# 리그 치수 (cm) — docs/frame.html. 원점 = 회전축과 회전판 윗면이 만나는 점, y 가 위쪽
CAM_DIST, CAM_Y, AIM_Y = 30.0, (16.0, 26.0), 24.0   # 축~렌즈 거리 · 렌즈 높이(아래, 가운데) · 두 카메라가 겨누는 높이
FOV = 53.5                                          # 카메라 긴 변 화각 (5MP OV5647)
SOIL_Y, POT_R, RIM_Y = 11.0, 7.6, 12.4              # 흙 높이 · 화분 테두리 반지름 · 테두리 높이
W, H, VOX = 240, 320, 0.25                          # 처리할 때 사진 크기 · 3D 한 칸(cm)


class Oops(Exception):
    pass


# ───────────── 찍기 ─────────────
def ask(url, path, timeout=30):
    try:
        with urlopen(f"{url}/{path}", timeout=timeout) as r:
            body = r.read()
    except OSError as e:
        raise Oops(f"{url} 에 연결하지 못했어요 — 전원 · Wi-Fi · config.env 의 주소 ({e})")
    if path == "photo" and body[:2] == b"\xff\xd8":
        return body
    d = json.loads(body)
    if not d.get("ok"):
        raise Oops(d.get("error", "실패"))
    return d


def shoot(out):
    """한 바퀴를 찍어 out 에 넣는다 (카메라 2대 = 24장) → 알릴 것(회전판이 밀렸는지, 카메라 한 대로 찍었는지)."""
    notes, lost = [], None                      # lost = 가운데 카메라(Pi ②)를 못 쓰는 까닭. 그러면 아래 카메라 12장으로 한다
    try:
        ask(PI2, "state", 5)
    except Oops as e:
        lost = str(e)
    ask(PI1, "hold?sec=300")                    # 스캔하는 동안 조명 켜고 팬 끄기 (5분 뒤 저절로 풀린다)
    try:
        ask(PI1, "home", 90)
        time.sleep(2)                           # 조명이 켜진 뒤 카메라 노출이 자리 잡게
        for deg in range(0, 360, 30):
            ask(PI1, f"goto?deg={deg}")
            time.sleep(1)                       # 잎 흔들림이 멎게
            (out / f"c0_{deg:03d}.jpg").write_bytes(ask(PI1, "photo"))
            if not lost:
                try:
                    (out / f"c1_{deg:03d}.jpg").write_bytes(ask(PI2, "photo"))
                except Oops as e:
                    lost = str(e)
            print(f"  {deg}° 찍음", file=sys.stderr)
        # 모터는 '돌렸다'고만 알지 실제로 돌았는지는 모른다. 그래서 끝에 원점 센서로 확인한다:
        # 330° 까지 제대로 돌았으면 원점까지 딱 30° 남아 있어야 한다
        # 덜 돌았으면(스텝을 놓쳤으면) 한 바퀴 동안 고르게 밀렸다고 보고, 사진마다 실제 각도를 고쳐 쓴다 (turn.json)
        try:
            back = ask(PI1, "home", 90)["moved_deg"]
            if 10 <= back <= 120:
                (out / "turn.json").write_text(json.dumps({"back_deg": back, "scale": round(360 / (330 + back), 4)}))
                if abs(back - 30) > 5:
                    notes.append(f"회전판이 덜 돌았어요 (360° 를 돌리는 데 {330 + back:g}° 만큼 시켜야 했어요) — 사진마다 실제 각도로 고쳐 계산했어요. "
                                 "늘 그러면 pi/pi.py 의 STEP_S 를 키우세요 (느리게 = 힘이 세게)")
            else:
                notes.append(f"회전판이 많이 밀렸어요 (원점까지 30° 가 아니라 {back:g}° 남아 있었어요) — 3D 가 흐릴 수 있어요. "
                             "화분이 가운데 있는지, 판이 어디 걸리지 않는지, 커플링을 보세요")
        except Oops as e:
            notes.append(f"스캔 뒤 원점을 다시 찾지 못했어요 — 회전판이 돌지 않은 것 같아요 ({e})")
    finally:
        try:
            ask(PI1, "hold?sec=0")
        except Oops:
            pass
    if lost:
        for p in out.glob("c1_*.jpg"):          # 한 바퀴를 다 못 찍은 카메라의 사진은 쓰지 않는다 (보정이 틀어진다)
            p.unlink()
        notes.append(f"가운데 카메라(Pi ②)를 쓰지 못해 아래 카메라 한 대로만 3D 를 만들었어요 — {lost}")
    return notes


# ───────────── 사진 → 3D ─────────────
def view(cam, deg):
    """회전판이 deg 도 돌았을 때, 식물 좌표의 점을 카메라 cam 좌표(x 오른쪽 · y 아래 · z 앞)로 옮기는 R, t."""
    pos = np.array([0.0, CAM_Y[cam], CAM_DIST])
    z = np.array([0.0, AIM_Y, 0.0]) - pos
    z /= np.linalg.norm(z)
    x = np.cross(z, [0.0, 1.0, 0.0])
    x /= np.linalg.norm(x)
    R = np.stack([x, np.cross(z, x), z])
    a = math.radians(deg * TURN_SCALE)        # 사진 이름의 각도는 '시킨' 각도. 덜 돌았으면 turn.json 의 비율로 고친다
    turn = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    return (R @ turn).astype(np.float32), (-R @ pos).astype(np.float32)


TURN_SCALE = 1.0    # 시킨 각도 → 실제 각도 (스캔마다 process() 가 turn.json 에서 읽는다)
FIX = {}        # 카메라마다 사진 보정값 [가로 px, 세로 px, 기울기, 배율] — 스캔마다 align() 이 사진에서 찾아 채운다


def project(P, cam, deg):
    """식물 좌표의 점들 (N,3) → 사진 속 픽셀 (u, v)."""
    R, t = view(cam, deg)
    X = P @ R.T + t
    du, dv, tilt, zoom = FIX.get(cam, (0.0, 0.0, 0.0, 1.0))
    f = zoom * (H / 2) / math.tan(math.radians(FOV) / 2)
    x, y = f * X[:, 0] / X[:, 2], f * X[:, 1] / X[:, 2]
    return x + tilt * y + du + (W - 1) / 2, y - tilt * x + dv + (H - 1) / 2


def near(m, k):
    """3×3 이웃 가운데 k 칸 이상이 참인 곳."""
    p = np.pad(m, 1).astype(np.uint8)
    return sum(p[i:i + m.shape[0], j:j + m.shape[1]] for i in range(3) for j in range(3)) >= k


def plant_mask(rgb):
    """식물 픽셀: 밝기와 상관없는 '초록 정도'(ExG − ExR)가 기준보다 큰 곳. 점 잡음은 지우고 작은 구멍은 메운다."""
    f = rgb.astype(np.float32) / 255
    s = f.sum(2) + 1e-6
    r, g, b = f[..., 0] / s, f[..., 1] / s, f[..., 2] / s
    return near(((2 * g - r - b) - (1.4 * r - g) > GREEN) & (s > 0.12), 5)


def behind_rim(P, cam, deg):
    """카메라에서 볼 때 화분 테두리에 가리는 점. 가려서 안 보인 것을 '식물 아님'으로 세지 않으려고."""
    R, t = view(cam, deg)
    c = -R.T @ t
    d = P - c
    a = d[:, 0] ** 2 + d[:, 2] ** 2
    b = 2 * (c[0] * d[:, 0] + c[2] * d[:, 2])
    disc = b * b - 4 * a * (c[0] ** 2 + c[2] ** 2 - POT_R ** 2)
    s = (-b - np.sqrt(np.maximum(disc, 0))) / (2 * np.maximum(a, 1e-9))
    return (disc >= 0) & (s > 0) & (s < 1) & (c[1] + s * d[:, 1] < RIM_Y)


def grid(vox, lo=(-16.0, SOIL_Y, -16.0), hi=(16.0, SOIL_Y + 34, 16.0)):
    """공간을 한 변 vox cm 인 칸으로 채운 점들 (N,3) 과 칸 수 (nx, ny, nz)."""
    axes = [np.arange(a + vox / 2, b, vox, dtype=np.float32) for a, b in zip(lo, hi)]
    return np.stack([g.ravel() for g in np.meshgrid(*axes, indexing="ij")], 1), [len(a) for a in axes]


def in_plant(P, fat, rim=True):
    """식물 안에 있는 점: 그 점이 찍힌 사진마다 식물 픽셀 위에 떨어진다 (한 장까지는 틀려도 봐준다)."""
    low = P[:, 1] < RIM_Y
    miss, seen, framed = (np.zeros(len(P), np.int16) for _ in range(3))
    for key, m in fat.items():
        u, v = project(P, *key)
        iu, iv = np.rint(u).astype(np.int32), np.rint(v).astype(np.int32)
        inside = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)
        hit = np.zeros(len(P), bool)
        hit[inside] = m[iv[inside], iu[inside]]
        hidden = np.zeros(len(P), bool)
        if rim:
            hidden[low] = behind_rim(P[low], *key)
        framed += inside
        seen += inside & (hit | ~hidden)
        miss += inside & ~hit & ~hidden
    # 사진 대부분에 찍힌 점만 믿는다 (사진 밖으로 나가는 곳은 깎을 근거가 없어 헛것이 남는다)
    return (miss <= 1) & (seen >= min(6, len(fat) // 2)) & (framed >= 0.6 * len(fat))


def align(masks, fat):
    """카메라가 몇 mm 비뚤게 달려 있어도 3D 가 깎여 나가지 않게, 사진에서 보정값을 찾아 FIX 에 넣는다.

    ① 가로 · 기울기 (카메라마다): 화분이 한 바퀴 돌면 식물은 회전축 좌우로 똑같이 지나간다 →
       식물 픽셀의 가로 평균을 줄마다 구하면 그것이 사진 속 회전축이다. 사진 가운데 세로선과의 차이가 어긋남.
    ② 세로 (가운데 카메라를 아래 카메라에 맞춘다): 두 카메라가 위아래로 어긋나면 3D 가 깎여 작아진다 →
       가운데 카메라 사진을 위아래로 옮겨 가며 3D 가 가장 크게 남는 값을 고른다.
    """
    FIX.clear()
    cams = sorted({cam for cam, _ in masks})
    for cam in cams:
        sx, n = np.zeros(H), np.zeros(H)
        for (c, _), m in masks.items():
            if c == cam:
                sx += m @ np.arange(W)
                n += m.sum(1)
        rows = np.nonzero(n >= 12)[0]
        du = tilt = 0.0
        if len(rows) >= 8:
            x, y = sx[rows] / n[rows] - (W - 1) / 2, rows - (H - 1) / 2
            if np.ptp(rows) >= 50:                  # 식물이 충분히 길어야 기울기를 믿을 수 있다
                tilt, du = np.polyfit(y, x, 1, w=np.sqrt(n[rows]))
            else:
                du = np.average(x, weights=n[rows])
        FIX[cam] = [float(du), 0.0, float(np.clip(tilt, -0.1, 0.1)), 1.0]
    if cams != [0, 1]:
        return
    P, _ = grid(0.5)
    P = P[in_plant(P, {k: m for k, m in fat.items() if k[0] == 0}, rim=False)]     # 아래 카메라만으로 본 식물 자리
    if len(P) < 20:
        return
    P, _ = grid(0.5, P.min(0) - 1.5, P.max(0) + 1.5)

    def size(dv):
        FIX[1][1] = float(dv)
        return int(in_plant(P, fat, rim=False).sum()), -abs(dv), dv

    best = max(size(dv) for dv in range(-16, 17, 2))
    FIX[1][1] = float(max(size(best[2] + a) for a in (-1, 0, 1))[2])


def carve(photos, masks):
    """→ 겉면 점 (N,3) · 색 (N,3) · 키 · 폭."""
    fat = {k: near(m, 1) for k, m in masks.items()}         # 한 픽셀 넉넉하게
    align(masks, fat)
    P, shape = grid(VOX)
    occ = in_plant(P, fat).reshape(shape)
    if occ.sum() < 20:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint8), None, None
    p = np.pad(occ, 1)                           # 겉면 = 여섯 이웃 중 하나라도 비어 있는 칸
    inner = p[2:, 1:-1, 1:-1] & p[:-2, 1:-1, 1:-1] & p[1:-1, 2:, 1:-1] & p[1:-1, :-2, 1:-1] & p[1:-1, 1:-1, 2:] & p[1:-1, 1:-1, :-2]
    all_pts = P[occ.ravel()]
    pts = P[(occ & ~inner).ravel()]
    rgb, n = np.zeros((len(pts), 3)), np.zeros(len(pts))
    for key, m in masks.items():                 # 색: 그 점이 식물로 찍힌 사진들의 평균
        u, v = project(pts, *key)
        iu, iv = np.rint(u).astype(np.int32), np.rint(v).astype(np.int32)
        ok = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)
        ok[ok] = m[iv[ok], iu[ok]]
        rgb[ok] += photos[key][iv[ok], iu[ok]]
        n[ok] += 1
    colors = np.where(n[:, None] > 0, rgb / np.maximum(n, 1)[:, None], (70, 140, 60)).astype(np.uint8)
    height = float(all_pts[:, 1].max()) + VOX / 2 - SOIL_Y
    width = float(max(np.ptp(all_pts[:, 0]), np.ptp(all_pts[:, 2]))) + VOX
    return pts, colors, round(height, 1), round(width, 1)


def write_ply(path, pts, colors):
    """색 점군 PLY (cm). three.js PLYLoader · MeshLab 에서 바로 열린다."""
    rec = np.zeros(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    for i, k in enumerate("xyz"):
        rec[k] = pts[:, i] if len(pts) else 0
    for i, k in enumerate("rgb"):
        rec[k] = colors[:, i] if len(pts) else 0
    head = ("ply\nformat binary_little_endian 1.0\n"
            f"element vertex {len(pts)}\nproperty float x\nproperty float y\nproperty float z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    Path(path).write_bytes(head.encode() + rec.tobytes())


def process(out, notes=()):
    global TURN_SCALE
    try:
        TURN_SCALE = float(json.loads((out / "turn.json").read_text())["scale"])
    except (OSError, ValueError, KeyError, TypeError):
        TURN_SCALE = 1.0
    photos = {}
    for p in sorted(out.glob("c?_???.jpg")):
        im = Image.open(p).convert("RGB")
        if ROTATE:
            im = im.rotate(-ROTATE, expand=True)
        photos[(int(p.name[1]), int(p.name[3:6]))] = np.asarray(im.resize((W, H)))
    if len(photos) < 12:
        raise Oops(f"사진이 {len(photos)}장뿐이에요 (12장 이상 있어야 3D 를 만들어요)")
    masks = {k: plant_mask(v) for k, v in photos.items()}
    pts, colors, height, width = carve(photos, masks)
    write_ply(out / "cloud.ply", pts, colors)
    sheet = Image.new("RGB", (12 * W // 2, 2 * H // 2))          # 점검용: 식물로 잡힌 곳만 밝게 보이는 사진 모음
    for (cam, deg), rgb in photos.items():
        dim = (rgb * np.where(masks[(cam, deg)][..., None], 1.0, 0.3)).astype(np.uint8)
        sheet.paste(Image.fromarray(dim).resize((W // 2, H // 2)), (deg // 30 * W // 2, cam * H // 2))
    sheet.save(out / "masks.jpg", quality=85)
    notes = list(notes)
    if height is None:
        notes.append("식물이 보이지 않아요 (빈 화분이면 정상이에요)")
    if np.mean([m.mean() for m in masks.values()]) > 0.4:
        notes.append("배경이 식물로 잡혔어요 — masks.jpg 를 보고 config.env 의 GREEN 을 올리세요")
    if sum(m[0].any() or m[:, 0].any() or m[:, -1].any() for m in masks.values()) >= 3:
        notes.append("식물이 사진 밖으로 나가요 — 키 · 폭이 실제와 다를 수 있어요")
    cm = CAM_DIST * 2 * math.tan(math.radians(FOV) / 2) / H             # 회전축 자리에서 1픽셀의 크기
    align = {f"c{cam}": {"side_cm": round(du * cm, 1), "up_cm": round(-dv * cm, 1), "tilt_deg": round(math.degrees(tilt), 1)}
             for cam, (du, dv, tilt, _) in FIX.items()}                 # 카메라가 얼마나 비뚤게 달렸는지 (보정한 값)
    for cam, a in align.items():
        if abs(a["side_cm"]) > 1.5 or abs(a["up_cm"]) > 1.4 or abs(a["tilt_deg"]) > 4:
            notes.append(f"{'아래' if cam == 'c0' else '가운데'} 카메라가 많이 비뚤어요 (옆으로 {a['side_cm']:g} cm, "
                         f"기울기 {a['tilt_deg']:g}°) — 보정해서 계산했지만, 카메라를 화분 가운데로 맞추면 더 정확해요")
    ts = datetime.fromtimestamp(min(p.stat().st_mtime for p in out.glob("c?_???.jpg"))).astimezone()
    day = round((ts - datetime.fromisoformat(SOWN).astimezone()).total_seconds() / 86400, 2) if SOWN else None
    meta = {"scan_id": out.name, "ts": ts.isoformat(timespec="seconds"), "day": day, "height_cm": height,
            "canopy_cm": width, "photos": len(photos), "points": len(pts), "cloud": "cloud.ply",
            "masks": "masks.jpg", "align": align, "turn_scale": TURN_SCALE, "notes": notes}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    write_index(out.parent)
    return meta


def write_index(scans):
    """스캔 목록 index.json (예측 · 대시보드가 읽는다). 남아 있는 스캔 폴더로 매번 다시 만든다 — 잘못 찍은 스캔은 폴더를 지우면 빠진다."""
    rows = []
    for f in scans.glob("*/meta.json"):
        try:
            m = json.loads(f.read_text())
            rows.append({k: m.get(k) for k in ("scan_id", "ts", "day", "height_cm", "canopy_cm", "notes")})
        except ValueError:
            pass
    (scans / "index.json").write_text(json.dumps(sorted(rows, key=lambda r: r["ts"] or ""), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            out = Path(sys.argv[1]).resolve()
        else:
            (DATA / "scans").mkdir(parents=True, exist_ok=True)
            busy = open(DATA / "scans" / ".lock", "w")
            try:                                    # 자동 스캔 · 대시보드 스캔 · 손으로 부른 스캔이 겹치지 않게 (회전판은 하나)
                import fcntl
                fcntl.flock(busy, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except ImportError:
                pass                                # 윈도우: 잠금 없이
            except OSError:
                raise Oops("다른 스캔이 지금 스캔하는 중이에요 — 1~2분 뒤에 다시")
            out = DATA / "scans" / time.strftime("%Y%m%d-%H%M%S")
            out.mkdir(parents=True)
            notes = shoot(out)
        meta = process(out, notes if len(sys.argv) == 1 else ())
        print(json.dumps({"ok": True, "scan_id": meta["scan_id"], "height_cm": meta["height_cm"],
                          "canopy_cm": meta["canopy_cm"], "json": str(out / "meta.json"),
                          "ply": str(out / "cloud.ply"), "notes": meta["notes"]}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e) if isinstance(e, Oops) else f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)
