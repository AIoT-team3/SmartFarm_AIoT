#!/usr/bin/env python3
"""생장 예측 (foresee): 우리 화분의 스캔 · 온습도 · 조명 · 급수 기록 → 앞으로 28일의 키(범위) · 수확 예상일.

맞히는 것은 하나다 — "이 크기의 식물이 이런 환경에서 하루에 얼마나 자라나". 세 가지가 겹쳐 있다:

  ① 처음에 믿는 것        식물마다의 생장식 (아래 PLANTS: 온도 · 빛 · 건조함 · 크기 → 하루 생장률). 어림값이어도 된다.
     + 사전학습           같은 식물의 재배 기록이 있으면 작은 신경망 8개가 "실제로는 이만큼 다르더라"를 곱한다 (train.py → model.json).
                          여덟 답이 갈리는 만큼이 예측의 범위에 더해진다.
  ② 우리 화분에서 배운다  스캔할 때마다 세 가지를 다시 맞춘다 — 빠르기(처음 예상의 몇 배인가) ·
                          자라는 꼴(클수록 빨라지나, 꾸준히 자라나) · 다 자란 키(어디서 느려지나).
                          그래서 ①이 틀려도 스캔이 쌓이면 따라간다. 스캔의 오차도 감안하고(칼만 필터),
                          앞뒤와 너무 다른 스캔은 한 번은 의심한다 (다음 스캔도 그 키면 그때 따라간다).
  ③ 스스로 채점한다       지난 스캔 때로 돌아가 "그때 예측했다면 그 뒤의 스캔을 얼마나 맞혔나"를 센다 (skill · checks).
                          두 스캔을 직선으로 이어 본 것과 견주고, 그보다 못하면 그렇다고 적는다.

그다음 마지막 스캔 때의 키에서 출발해 하루씩 키운다 (앞날의 환경은 최근 3일과 같다고 본다).
'만약에'(조명 ±2시간 · 기온 ±2 °C · 물 ±20 mL)는 status 가 다음 행동을 고를 때 쓴다.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

E = os.environ.get
DATA = Path(E("DATA", "data")).resolve()
SOWN = E("SOWN", "")
PLANT = E("PLANT", "basil")
MODEL = Path(E("MODEL", str(Path(__file__).with_name("model.json"))))
PLANTS = {
    # 처음에 믿는 값이다. 스캔이 쌓이면 우리 화분에 맞춰지므로 어림값이어도 된다.
    #   이름 · 수확할 키 · 다 자란 키 (cm) · 하루 생장률 (가장 좋은 온도 · 조명 16시간 · 키 8 cm 일 때)
    #   · 자라는 꼴 (1 = 클수록 빨라진다 ~ 0 = 꾸준히 자란다) · 온도 °C (이 아래로는 안 자람, 여기부터, 여기까지 가장 잘 자람, 이 위로는 안 자람)
    "basil": ("바질", 16.0, 40.0, 0.13, 1.0, (11.0, 25.0, 35.0, 42.0)),         # Walters 외 2023: 11 °C 부터 자라고 25~35 °C 에서 가장 잘
    # 대파 밑동을 잘라 다시 키운다: 키 = 흙(하이드로볼) 윗면에서 초록 끝까지. 처음부터 15 cm 안팎이고 하루 1 cm 안팎으로 꾸준히 자라다가 45 cm 쯤에서 멎는다.
    # 수확할 키 24 cm 는 카메라가 볼 수 있는 높이(흙 위 약 26 cm) 안쪽으로 잡았다 — config.env 의 TARGET_CM 으로 바꾼다
    "greenonion": ("대파", 24.0, 45.0, 0.19, 0.0, (5.0, 19.0, 27.0, 38.0)),      # Brewster 1979: 19 °C 에서 가장 잘 · 27 °C 까지 비슷 · 31 °C 면 느려짐
}
if PLANT not in PLANTS:
    print(json.dumps({"ok": False, "error": f"config.env 의 PLANT 는 {' · '.join(PLANTS)} 가운데 하나여야 해요 (지금: {PLANT})"}, ensure_ascii=False))
    sys.exit(1)
NAME, TARGET, FULL, RATE, FORM, TEMP = PLANTS[PLANT]
TARGET = float(E("TARGET_CM") or TARGET)                    # 수확할 키를 config.env 에서 바꿀 수 있다
VIEW_TOP = 26.0                     # 카메라가 키를 잴 수 있는 높이 (흙 위 cm, camera.py 의 구도) — 넘으면 스캔이 '사진 밖' 이라고 적는다
SOIL_Y = 11.0                       # 흙 높이 (camera.py 와 같은 좌표)
H_REF = 8.0                         # 생장률의 기준이 되는 키 (cm)
ERR = 0.6                           # 스캔 키의 오차 (cm)
PPFD = 200.0                        # 우리 조명의 밝기 (µmol/m²/s) — 전구 4개 기준 어림값. 재 보면 고친다
TYPICAL = {"temp_c": 22.0, "rh_pct": 60.0, "light_h": 16.0, "water_ml": 50.0}       # 기록이 없을 때 쓰는 값
AHEAD = 150                         # 수확일을 찾느라 내다보는 날 수
PACE = np.linspace(-1.6, 1.6, 33)   # '빠르기' 후보 (로그): 처음 예상의 0.2배 ~ 5배
FORMS = np.linspace(0, 1, 5)        # '자라는 꼴' 후보: 0 꾸준히(직선) ~ 1 클수록 빨라짐(지수)
FULLS = np.array([0.5, 0.75, 1.0, 1.5])     # '다 자란 키' 후보: PLANTS 에 적힌 값의 몇 배


# ───────────── 생장 모델: 생장식 × 신경망 보정 ─────────────
def dli_of(light_h):
    """조명 시간 → 하루 광량 (mol/m²)."""
    return PPFD * np.asarray(light_h, float) * 0.0036


def formula(temp, rh, dli, size, form=None, full=None):
    """생장식: 하루 상대 생장률 = 기준 × 온도 × 빛 × 건조함 × 크기. form(자라는 꼴) · full(다 자란 키)을 안 주면 PLANTS 의 값."""
    temp, rh, dli = (np.asarray(x, float) for x in (temp, rh, dli))
    size = np.maximum(np.asarray(size, float), 0.3)
    lo, best_from, best_to, hi = TEMP
    f_temp = np.clip(np.minimum((temp - lo) / (best_from - lo), (hi - temp) / (hi - best_to)), 0, 1)
    f_light = (1 - np.exp(-dli / 6)) / (1 - np.exp(-dli_of(16) / 6))    # 조명 16시간을 1 로
    vpd = 0.6108 * np.exp(17.27 * temp / (temp + 237.3)) * (1 - rh / 100)
    f_dry = np.clip(1 - 0.25 * np.maximum(0, vpd - 1.5), 0.5, 1)        # 공기가 너무 건조하면 덜 자란다
    f_size = (size / H_REF) ** (np.asarray(FORM if form is None else form, float) - 1) * np.maximum(0, 1 - size / (FULL if full is None else full))
    return RATE * f_temp * f_light * f_dry * f_size


def inputs(temp, rh, dli, water, size, ranges=None):
    """신경망에 넣는 다섯 값 (N,5). 학습 때 본 범위를 벗어나면 그 끝값으로 본다 — 못 본 곳을 지어내지 않게."""
    cols = dict(temp_c=temp, rh_pct=rh, dli=dli, water_ml=water, size=size)
    n = max(np.size(v) for v in cols.values())
    cols = {k: np.broadcast_to(np.asarray(v, float), (n,)) for k, v in cols.items()}
    if ranges:
        cols = {k: np.clip(v, *ranges[k]) for k, v in cols.items()}
    return np.stack([(cols["temp_c"] - 22) / 6, (cols["rh_pct"] - 60) / 20, (cols["dli"] - 11.5) / 6,
                     (cols["water_ml"] - 50) / 50, np.log(np.maximum(cols["size"], 0.3) / 8)], 1)


def correction(z, net):
    """신경망 하나의 답: 생장식에 곱할 값 (0.37 ~ 2.7 배 사이)."""
    hidden = np.tanh(z @ np.asarray(net["W1"]) + np.asarray(net["b1"]))
    return np.exp(np.tanh(hidden @ np.asarray(net["W2"]) + net["b2"]))


def growth(temp, rh, dli, water, size, net=None, ranges=None, form=None, full=None):
    """하루 상대 생장률. net 이 없으면 생장식만."""
    g = formula(temp, rh, dli, size, form, full)
    return g if net is None else g * correction(inputs(temp, rh, dli, water, size, ranges), net)


def grown(h, r, form):
    """하루 뒤의 키. 생장률 r 로 — form 1 이면 지수로 (h·e^r), 0 이면 직선으로 (h + r·h), 그 사이도 된다."""
    e = 1 - np.asarray(form, float)
    return h * np.where(e < 1e-6, np.exp(r), (1 + e * r) ** (1 / np.maximum(e, 1e-6)))


def load_model():
    """→ (모델 또는 None, 못 쓰는 까닭 또는 None)."""
    if not MODEL.exists():
        return None, "학습한 모델(foresee/model.json)이 없어 생장식과 우리 화분의 스캔만으로 예측했어요"
    try:
        m = json.loads(MODEL.read_text())
        if m.get("plant", "basil") != PLANT:
            return None, f"학습한 모델이 다른 식물({m.get('plant', 'basil')})의 것이라 쓰지 않았어요 — {NAME} 기록으로 train.sh 를 다시 돌리세요"
        if m["use"] and m["nets"] and m["ranges"]:
            return m, None
        return None, "학습한 모델이 생장식보다 낫지 않아서 쓰지 않았어요"
    except (OSError, ValueError, KeyError):
        return None, "학습한 모델 파일(foresee/model.json)을 읽지 못해 쓰지 않았어요"


# ───────────── 우리 화분의 기록 ─────────────
def daily_env():
    """climate.jsonl · water.jsonl → {날짜: {temp_c, rh_pct, light_h, water_ml}} (그날 평균 · 조명 시간 · 준 물)."""
    days = {}
    f = DATA / "climate.jsonl"
    for line in f.read_text().splitlines() if f.exists() else []:
        try:
            r = json.loads(line)
            d = days.setdefault(r["ts"][:10], {"t": [], "rh": [], "light": [], "water": 0.0})
        except (ValueError, KeyError):
            continue
        if r.get("temp_c") is not None:
            d["t"].append(float(r["temp_c"]))
            d["rh"].append(float(r["rh_pct"]))
        if r.get("light") is not None:
            d["light"].append(float(r["light"]))
    f = DATA / "water.jsonl"
    for line in f.read_text().splitlines() if f.exists() else []:
        try:
            r = json.loads(line)
            if r.get("by") != "test":                               # 유량을 재느라 컵에 받은 물은 화분에 준 것이 아니다
                days.setdefault(r["ts"][:10], {"t": [], "rh": [], "light": [], "water": 0.0})["water"] += float(r.get("ml") or 0)
        except (ValueError, KeyError):
            pass
    mean = lambda v, default: sum(v) / len(v) if v else default
    return {day: {"temp_c": mean(d["t"], None), "rh_pct": mean(d["rh"], None),
                  "light_h": 24 * mean(d["light"], None) if len(d["light"]) >= 100 else None,    # 하루치가 거의 다 있을 때만 (10분마다 한 줄)
                  "water_ml": d["water"]}
            for day, d in days.items()}


def quantiles(values, weights, qs=(0.1, 0.5, 0.9)):
    order = np.argsort(values)
    return np.interp(qs, np.cumsum(weights[order]), values[order])


def basil(height, step=0.25, seed=0):
    """키 height cm 인 바질 모양의 점과 색: 줄기 + 마디마다 마주난 잎 한 쌍(층마다 90° 돌려 난다)."""
    rng = np.random.default_rng(seed)
    y = np.arange(0, height + 1e-6, step)
    a = np.arange(0, 2 * np.pi, np.pi / 4)
    P = [np.stack([0.18 * np.cos(a)[None, :] + 0 * y[:, None], y[:, None] + 0 * a[None, :],
                   0.18 * np.sin(a)[None, :] + 0 * y[:, None]], -1).reshape(-1, 3)]
    C = [np.tile([105.0, 140.0, 70.0], (len(P[0]), 1))]
    n = max(1, int(height / 2.2))                                   # 마디 수
    for k in range(n):
        y0 = height * (k + 1) / (n + 0.6)                           # 마디 높이
        L = (1.5 + 0.28 * height) * (1 - 0.45 * k / n)              # 잎 길이: 아래 잎이 크고 위는 어리다
        for side in (0.0, np.pi):
            ang = k * np.pi / 2 + side + rng.normal(0, 0.15)
            out, across = np.array([np.cos(ang), 0, np.sin(ang)]), np.array([-np.sin(ang), 0, np.cos(ang)])
            u, v = np.meshgrid(np.arange(0.04, 1, step / L), np.arange(-1, 1.001, step / (0.32 * L)), indexing="ij")
            half = 0.32 * L * np.sin(np.pi * u) ** 0.7              # 잎 폭 (가운데가 넓다)
            lift = y0 + L * (0.35 * u - 0.45 * u * u) + 0.25 * half * v * v     # 살짝 들렸다 끝이 처지고, 가장자리가 말린다
            P.append((out * (0.3 + u * L)[..., None] + across * (half * v)[..., None] + np.array([0, 1, 0]) * lift[..., None]).reshape(-1, 3))
            shade = (0.85 + 0.3 * u + 0.12 * (1 - np.abs(v)) + rng.normal(0, 0.03, u.shape)).reshape(-1, 1)
            C.append(np.array([62.0, 138.0, 58.0]) * shade)
    P, C = np.concatenate(P), np.concatenate(C)
    P[:, 1] += SOIL_Y
    return P.astype(np.float32), np.clip(C, 0, 255).astype(np.uint8)


def greenonion(height, step=0.25, seed=0):
    """키 height cm 인 파 모양의 점과 색: 가운데에서 솟는 대롱 잎 여러 장 (바깥 잎일수록 짧고 더 벌어진다)."""
    rng = np.random.default_rng(seed)
    P, C = [], []
    n = int(np.clip(2 + height / 7, 2, 6))                         # 잎 수
    for k in range(n):
        ang = k * 2.4 + rng.normal(0, 0.2)                          # 돌아가며 난다
        top, lean = height * (1 - 0.16 * k), 0.05 + 0.07 * k        # 잎 끝의 높이 · 벌어지는 정도
        s, a = np.meshgrid(np.arange(0, 1.0001, step / max(top, 1)), np.arange(0, 2 * np.pi, min(np.pi / 4, 2 * step)), indexing="ij")
        radius = (0.45 - 0.05 * k) * (1 - s) ** 0.6 + 0.05          # 끝으로 갈수록 가늘다
        out = lean * top * s * s                                    # 위로 갈수록 바깥으로 휜다
        P.append(np.stack([np.cos(ang) * (0.25 * k + out) + radius * np.cos(a), top * s,
                           np.sin(ang) * (0.25 * k + out) + radius * np.sin(a)], -1).reshape(-1, 3))
        green = np.array([70.0, 140.0, 75.0]) * (0.9 + 0.25 * s + rng.normal(0, 0.03, s.shape))[..., None]
        white = np.array([225.0, 230.0, 205.0]) + 0 * green
        C.append(np.where((top * s < 0.12 * height)[..., None], white, green).reshape(-1, 3))      # 밑동은 희다
    P, C = np.concatenate(P), np.concatenate(C)
    P[:, 1] += SOIL_Y
    return P.astype(np.float32), np.clip(C, 0, 255).astype(np.uint8)


def write_ply(path, pts, colors):
    rec = np.zeros(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    for i, k in enumerate("xyz"):
        rec[k] = pts[:, i]
    for i, k in enumerate("rgb"):
        rec[k] = colors[:, i]
    head = ("ply\nformat binary_little_endian 1.0\n"
            f"element vertex {len(pts)}\nproperty float x\nproperty float y\nproperty float z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    Path(path).write_bytes(head.encode() + rec.tobytes())


def fail(text):
    print(json.dumps({"ok": False, "error": text}, ensure_ascii=False))
    sys.exit(1)


def read(path):
    try:
        return json.loads((DATA / path).read_text())
    except (OSError, ValueError):
        return None


def interp(h, days_after):
    """키 배열 (…, 날) 에서 시작 뒤 days_after 일째(소수) 값."""
    i = int(np.clip(np.floor(days_after), 0, h.shape[-1] - 2))
    f = float(np.clip(days_after - i, 0, 1))
    return h[..., i] * (1 - f) + h[..., i + 1] * f


def weights(score):
    w = np.exp(score - score.max())
    return w / w.sum()


def band(h, w, err):
    """후보들의 키 h 와 무게 w → (p10, p50, p90). 키를 모르는 정도 err(cm)만큼 범위를 넓힌다."""
    low, mid, high = quantiles(h, w)
    return [round(float(x), 2) for x in (mid - np.hypot(mid - low, 1.28 * err), mid, mid + np.hypot(high - mid, 1.28 * err))]


def main():
    if not SOWN:
        fail("config.env 에 SOWN(심은 날)을 적어 주세요")
    sown = datetime.fromisoformat(SOWN).astimezone()
    now = datetime.fromisoformat(E("NOW")).astimezone() if E("NOW") else datetime.now().astimezone()     # NOW: 서버 hub 가 지난 스캔 때로 돌아가 다시 계산할 때
    today = (now - sown).total_seconds() / 86400
    if today < 0:
        fail("SOWN(심은 날)이 오늘보다 뒤예요")
    model, why = load_model()
    nets, ranges = (model["nets"], model["ranges"]) if model else ([None], None)
    notes = [why] if why else []
    if model:
        flat = [k for k in ("temp_c", "rh_pct", "dli", "water_ml") if ranges[k][0] == ranges[k][1]]
        if flat:
            names = {"temp_c": "기온", "rh_pct": "습도", "dli": "광량", "water_ml": "급수량"}
            notes.append(f"학습 데이터에 {' · '.join(names[k] for k in flat)} 변화가 없어, 그 영향은 생장식대로만 봤어요")

    # 1) 날마다의 환경: 기록이 있는 날은 그 값, 없는 날과 앞날은 최근 3일 평균
    env = daily_env()
    if now.strftime("%Y-%m-%d") in env:                                     # 오늘은 아직 안 끝났다 → 조명 시간 · 급수량은 평소 값으로
        env[now.strftime("%Y-%m-%d")].update(light_h=None, water_ml=None)
    recent = [env[d] for d in sorted(env)[-4:]]                             # 오늘 + 지난 3일
    usual = {k: (lambda v: sum(v) / len(v) if v else TYPICAL[k])([e[k] for e in recent if e[k] is not None]) for k in TYPICAL}
    if not any(e["temp_c"] is not None for e in env.values()):
        notes.append(f"온도 기록이 없어 {TYPICAL['temp_c']:g} °C 로 계산했어요 (climate.sh 를 먼저 부르세요)")
    scans = sorted((r for r in read("scans/index.json") or [] if (r.get("height_cm") or 0) > 0 and r.get("day") is not None
                    and 0 <= r["day"] <= today + 1), key=lambda r: r["day"])
    if not scans:
        notes.append("키를 잰 스캔이 아직 없어 오늘 키를 3 cm 로 놓고 예측했어요")

    # 후보: (신경망) × (빠르기) × (자라는 꼴) × (다 자란 키). 후보마다 따로 키워 보고, 스캔과 잘 맞는 후보에 무게를 준다
    pace, form, full = (np.tile(x.ravel(), len(nets)) for x in np.meshgrid(PACE, FORMS, FULL * FULLS, indexing="ij"))
    per_net = len(PACE) * len(FORMS) * len(FULLS)

    def grow(h0, start, n, change=None):
        """키 h0 인 날(start: 심은 뒤 며칠째)부터 n 일을 하루씩 키운다 → 키 (후보 수, n + 1).
        change 는 '만약에'용: 내일부터의 환경에만 더한다."""
        date = [(sown + timedelta(days=start + i)).strftime("%Y-%m-%d") for i in range(n + 1)]
        e = {k: np.array([usual[k] if env.get(d, {}).get(k) is None else env[d][k] for d in date]) for k in TYPICAL}
        for k, add in (change or {}).items():
            e[k] = np.where(np.arange(n + 1) > int(today) - start, e[k] + add, e[k])
        temp, rh, dli, water = e["temp_c"], e["rh_pct"], dli_of(np.clip(e["light_h"], 0, 24)), np.maximum(e["water_ml"], 0)
        h = np.empty((len(pace), n + 1))
        h[:, 0] = h0
        for m, net in enumerate(nets):
            part = slice(m * per_net, (m + 1) * per_net)
            for d in range(n):
                r = growth(temp[d], rh[d], dli[d], water[d], h[part, d], net, ranges, form[part], full[part]) * np.exp(pace[part])
                h[part, d + 1] = grown(h[part, d], r, form[part])
        return h

    # 2) 우리 화분에서 배운다: 후보마다 스캔에서 스캔으로 키워 보고, 다음 스캔을 잘 맞힌 후보일수록 무게가 커진다.
    #    스캔도 오차가 있으므로 후보마다 '참 키'를 따로 따라간다 (예측한 키와 스캔한 키를, 서로의 믿을 만한 정도대로 섞는다 = 칼만 필터)
    prior = -0.5 * ((pace / 0.5) ** 2 + ((form - FORM) / 0.5) ** 2 + (np.log(full / FULL) / 0.5) ** 2)    # 처음에는 PLANTS 의 값 근처를 믿는다
    score, height, var = prior, np.full(len(pace), scans[0]["height_cm"] if scans else 3.0), np.full(len(pace), ERR ** 2 if scans else 0.0)
    seen = [(score, height, var)]                                           # seen[j] = 스캔 j 까지 본 뒤의 (점수, 참 키, 그 불확실함)
    for a, b in zip(scans, scans[1:]):
        lead = b["day"] - a["day"]
        guess = interp(grow(height, int(a["day"]), int(lead) + 1), lead)    # 스캔 a 에서 키워 본 스캔 b 때의 키
        var = var * (guess / height) ** (2 * form) + (0.15 * (guess - height)) ** 2 + 0.01 * lead     # 식이 틀릴 몫을 더한다
        off = b["height_cm"] - guess
        if any("사진 밖" in n for n in b.get("notes") or []):              # 사진 밖으로 나간 스캔의 키는 '적어도 이만큼' 이다:
            off = np.maximum(off, 0)                                        #   예측이 그보다 크면 틀린 게 아니다
        total = var + (ERR * (2 if b.get("notes") else 1)) ** 2             # 경고가 붙은 스캔(회전판 밀림 · 사진 밖)은 덜 믿는다
        odd = np.abs(off) > 3 * np.sqrt(total)                              # 앞뒤와 너무 다른 스캔은 한 번은 의심한다:
        if weights(score) @ odd > 0.5:
            b["suspect"] = True
            notes.append(f"{b['day']:g}일째 스캔({b['height_cm']:g} cm)은 앞뒤와 너무 달라 덜 믿었어요 — 잘못 찍혔으면 그 스캔 폴더를 지우세요")
        total = np.where(odd, off ** 2 / 9, total)                          #   거의 안 따라가되,
        score = score - 0.5 * (off ** 2 / total + np.log(total))
        trust = var / total                                                 # 스캔을 믿는 정도
        height, var = guess + trust * off, np.where(odd, var + off ** 2 / 4, (1 - trust) * var)     #   다음 스캔도 그 키면 그때 따라간다
        seen.append((score, height, var))
    weight = weights(score)
    blur = float(np.sqrt(weight @ var))
    if TARGET > VIEW_TOP - 1:
        notes.append(f"수확할 키({TARGET:g} cm)가 카메라가 잴 수 있는 높이(흙 위 약 {VIEW_TOP:g} cm)에 가까워요 — 그 뒤의 스캔은 '적어도 이만큼'으로만 봐요")                                     # 지금 키를 모르는 정도 (cm)

    # 3) 예측: 마지막 스캔 때의 참 키에서 출발한다. 곡선은 '며칠째' 0시마다의 키다 —
    #    스캔은 하루 중 아무 때나 찍히므로(08 · 13 · 18시), 스캔 때부터 키운 값을 그 시각에 맞춰 옮겨 적는다
    start = int(scans[-1]["day"]) if scans else int(today)
    now_i = int(today) - start
    frac = (scans[-1]["day"] - start) if scans else 0.0

    def ahead_of(change=None):
        h = grow(height, start, now_i + AHEAD + 1, change)
        return np.stack([interp(h, max(0.0, i - frac)) for i in range(now_i + AHEAD + 1)], 1)

    def curve_of(h):
        return [dict(zip(("p10", "p50", "p90"), band(h[:, d], weight, blur))) for d in range(h.shape[1])]

    def reach(curve, key):
        return next((i for i, c in enumerate(curve) if c[key] >= TARGET), None)

    ahead = ahead_of()
    curve = [{"day": start + i, "date": (sown + timedelta(days=start + i)).strftime("%Y-%m-%d"), **c} for i, c in enumerate(curve_of(ahead))]
    hit = {"p10": reach(curve, "p90"), "p50": reach(curve, "p50"), "p90": reach(curve, "p10")}     # 빠르면 · 보통 · 늦으면
    harvest = {"target_cm": TARGET, "ready": bool(scans) and curve[0]["p50"] >= TARGET,
               "day": {k: i if i is None else curve[i]["day"] for k, i in hit.items()},
               "date": {k: i if i is None else curve[i]["date"] for k, i in hit.items()},
               "days_left": None if hit["p50"] is None else max(0, curve[hit["p50"]]["day"] - int(today))}
    one = grow(height, start, 1)
    step = one[:, 1] - one[:, 0]                                            # 마지막 스캔 때부터 하루에 자라는 길이
    speed, expected = float(quantiles(step, weight, (0.5,))[0]), float(quantiles(step, weights(prior), (0.5,))[0])
    shape = float(weight @ form)
    personal = {"scans": len(scans), "speed_cm_per_day": round(speed, 2), "pace": round(speed / expected, 2) if expected > 1e-6 else None,
                "form": round(shape, 2), "full_cm": round(float(quantiles(full, weight, (0.5,))[0]), 1), "text": "스캔이 두 번 이상 쌓이면 우리 화분에 맞춰요"}
    if len(scans) > 1:
        kind = "꾸준히 자라는 편" if shape < 0.35 else "클수록 빨라지는 편" if shape > 0.65 else "꾸준함과 빨라짐의 중간"
        personal["text"] = (f"우리 화분({NAME})은 지금 하루에 약 {speed:.1f} cm 자라요"
                            + (f" — 처음 예상의 {personal['pace']:.2f}배 빠르기, {kind}" if personal["pace"] else f" — {kind}"))

    # 4) 스스로 채점: 스캔이 두 번 이상 쌓인 뒤의 스캔 때마다, 그때 아는 것만으로 그 뒤의 스캔을 예측했다면 얼마나 맞았나
    checks = []
    for j in range(1, len(scans) - 1):
        a, (score_j, height_j, var_j) = scans[j], seen[j]
        w = weights(score_j)
        h = grow(height_j, int(a["day"]), int(scans[-1]["day"] - a["day"]) + 2)
        slope = (a["height_cm"] - scans[j - 1]["height_cm"]) / max(a["day"] - scans[j - 1]["day"], 0.25)
        for b in scans[j + 1:]:
            lead = b["day"] - a["day"]
            if lead >= 0.5:                                                  # 같은 날 다시 찍은 것은 예측이 아니다
                low, mid, high = band(interp(h, lead), w, float(np.sqrt(w @ var_j + ERR ** 2)))    # 맞혀 볼 스캔에도 오차가 있다
                checks.append({"made_day": a["day"], "for_day": b["day"], "lead_days": round(lead, 1), "predicted_cm": mid, "low_cm": low,
                               "high_cm": high, "actual_cm": b["height_cm"], "straight_line_cm": round(a["height_cm"] + slope * lead, 1)})
    skill = None
    if checks:
        miss = lambda key: round(sum(abs(c[key] - c["actual_cm"]) for c in checks) / len(checks), 2)
        inside = sum(c["low_cm"] <= c["actual_cm"] <= c["high_cm"] for c in checks)
        skill = {"checks": len(checks), "error_cm": miss("predicted_cm"), "straight_line_error_cm": miss("straight_line_cm"), "inside_range": inside}
        skill["text"] = (f"지난 예측 {len(checks)}번을 채점했더니 평균 {skill['error_cm']:.1f} cm 틀렸어요 "
                         f"(두 스캔을 직선으로 이어 본 것은 {skill['straight_line_error_cm']:.1f} cm). 실제 키가 예측 범위 안에 든 것은 {inside}번이에요")
        if skill["error_cm"] > skill["straight_line_error_cm"]:
            notes.append("지금까지는 직선으로 이어 본 것보다 못 맞혔어요 — 스캔이 더 쌓이면 다시 보세요")

    # 5) 만약에: 내일부터 환경을 바꾸면 2주 뒤 키와 수확일이 얼마나 달라지나
    what_if = []
    cases = [("조명을 하루 2시간 더 켜면", {"light_h": 2}), ("조명을 하루 2시간 덜 켜면", {"light_h": -2}),
             ("기온이 2 °C 높으면", {"temp_c": 2}), ("기온이 2 °C 낮으면", {"temp_c": -2})]
    if model and ranges["water_ml"][0] < ranges["water_ml"][1]:            # 물의 영향은 학습 데이터에서 배웠을 때만 말한다
        cases += [("물을 하루 20 mL 더 주면", {"water_ml": 20}), ("물을 하루 20 mL 덜 주면", {"water_ml": -20})]
    for text, change in cases:
        other = curve_of(ahead_of(change))
        days = reach(other, "p50")
        what_if.append({"change": text, "height_14d_cm": round(other[now_i + 14]["p50"] - curve[now_i + 14]["p50"], 1),
                        "harvest_shift_days": None if days is None or hit["p50"] is None else days - hit["p50"]})

    # 6) 앞날의 3D 모양
    out = DATA / "foresee"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("plant_d*.ply"):
        old.unlink()
    plys = []
    for more in (0, 7, 14, 21, 28):
        c = curve[now_i + more]
        write_ply(out / f"plant_d{c['day']:03d}.ply", *SHAPES[PLANT](max(0.5, c["p50"]), step=0.15, seed=7))
        plys.append({"day": c["day"], "date": c["date"], "height_cm": c["p50"], "file": f"plant_d{c['day']:03d}.ply"})

    doc = {"made_at": now.isoformat(timespec="seconds"), "plant": PLANT, "plant_name": NAME, "sown": SOWN, "today": round(today, 2),
           "observed": [{"day": r["day"], "ts": r["ts"], "height_cm": r["height_cm"], "suspect": bool(r.get("suspect"))} for r in scans],
           "curve": curve[:now_i + 29], "harvest": harvest,
           "model": {"trained": bool(model), **({k: model[k] for k in ("made_at", "rows", "plants", "sources", "error")} if model else {})},
           "personal": personal, "skill": skill, "checks": checks[-40:],
           "env": {**{k: round(v, 1) for k, v in usual.items()}, "days_logged": len(env),
                   "text": f"앞날은 기온 {usual['temp_c']:.1f} °C · 습도 {usual['rh_pct']:.0f} % · 조명 {usual['light_h']:.0f}시간 · 물 하루 {usual['water_ml']:.0f} mL 로 봤어요"},
           "what_if": what_if, "plys": plys, "notes": notes}
    (out / "forecast.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    print(json.dumps({"ok": True, "json": str(out / "forecast.json"), "plant": PLANT, "harvest": harvest, "speed_cm_per_day": personal["speed_cm_per_day"],
                      "pace": personal["pace"], "trained": bool(model), "skill": skill and skill["text"], "notes": notes}, ensure_ascii=False))


SHAPES = {"basil": basil, "greenonion": greenonion}

if __name__ == "__main__":
    main()
