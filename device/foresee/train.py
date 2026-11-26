#!/usr/bin/env python3
"""사전학습: 같은 식물(config.env 의 PLANT)의 재배 기록으로 생장 모델을 가르쳐 foresee/model.json 을 만든다.  (맥북에서 한 번, 1분 안쪽)
공개 데이터든, 우리가 여러 포기를 날마다 자로 잰 표든 된다.

  train.sh [CSV 가 든 폴더]        기본 폴더는 foresee/dataset/

CSV 한 줄 = 어느 화분을 어느 날 잰 값. 같은 화분을 두 번 이상 잰 것만 쓸모가 있다 (그 사이에 얼마나 자랐나를 배운다).

  plant,day,size,temp_c,rh_pct,light_h,water_ml
  A1,0,3.2,24.1,58,16,40
  A1,7,6.9,24.6,61,16,40

  꼭 있어야 하는 칸   plant (화분 이름) · day (며칠째. 없으면 date 칸의 날짜 2026-11-17) · size (크기)
  있으면 좋은 칸      temp_c · rh_pct · light_h (조명 시간) 또는 dli (하루 광량 mol/m²) · water_ml (하루 급수량)
                      measure = height(기본, cm) | area(잎 면적 cm²) | weight(무게 g) — 키가 아니면 키에 견줄 값으로 바꾼다
  없는 칸은 보통 값(22 °C · 60 % · 조명 16시간 · 하루 50 mL)으로 본다. 그 칸의 영향은 못 배운다.

배우는 것: foresee.py 의 생장식이 실제와 얼마나 다른지 (작은 신경망 8개).
시험: 화분을 다섯 묶음으로 나눠, 한 묶음씩 빼고 가르친 뒤 그 묶음을 맞혀 본다. 생장식보다 못 맞히면
model.json 에 "use": false 로 적고, foresee 는 생장식으로 돌아간다.
"""
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from foresee import MODEL, PLANT, TYPICAL, correction, dli_of, formula, inputs

HIDDEN, NETS, STEPS = 12, 8, 1500
TO_HEIGHT = {"height": lambda s: s, "area": lambda s: s ** 0.5, "weight": lambda s: 4 * s ** (1 / 3)}     # 키에 견줄 값 (어림)
COLS = ("temp_c", "rh_pct", "dli", "water_ml", "size")


def load(folder):
    """CSV 들 → 구간 표: 화분마다 이어진 두 측정 사이의 (화분, 기온, 습도, 광량, 급수량, 처음 크기, 하루 상대 생장률)."""
    rows, sources = [], []
    for f in sorted(Path(folder).glob("*.csv")):
        plants = {}
        for r in csv.DictReader(f.open(encoding="utf-8-sig")):
            num = lambda k: float(r[k]) if (r.get(k) or "").strip() else None
            try:
                day = num("day") if num("day") is not None else datetime.fromisoformat(r["date"].strip()).timestamp() / 86400
                size = TO_HEIGHT[(r.get("measure") or "height").strip()](float(r["size"]))
                light = num("dli") if num("dli") is not None else None if num("light_h") is None else float(dli_of(num("light_h")))
                plants.setdefault(f"{f.stem}/{r['plant']}", []).append((day, size, num("temp_c"), num("rh_pct"), light, num("water_ml")))
            except (KeyError, ValueError, TypeError, AttributeError):
                continue                                    # 못 읽는 줄(빈 칸 · 글자)은 건너뛴다
        n0 = len(rows)
        for plant, pts in plants.items():
            pts.sort()
            for a, b in zip(pts, pts[1:]):
                if b[0] > a[0] and a[1] > 0 and b[1] > 0:
                    mean = lambda i, default: float(np.mean([x[i] for x in (a, b) if x[i] is not None] or [default]))
                    rows.append((plant, mean(2, TYPICAL["temp_c"]), mean(3, TYPICAL["rh_pct"]), mean(4, float(dli_of(TYPICAL["light_h"]))),
                                 mean(5, TYPICAL["water_ml"]), a[1], float(np.log(b[1] / a[1]) / (b[0] - a[0]))))
        if len(rows) > n0:
            sources.append(f"{f.name} ({len(rows) - n0}구간)")
    return rows, sources


def fit(z, base, y, seed):
    """신경망 하나를 가르친다: base × exp(tanh(신경망)) 이 y 에 가깝게 (Adam · 가중치 감쇠)."""
    rng = np.random.default_rng(seed)
    p = {"W1": rng.normal(0, 0.5, (z.shape[1], HIDDEN)), "b1": np.zeros(HIDDEN), "W2": np.zeros(HIDDEN), "b2": np.zeros(1)}
    m, v = {k: 0 * x for k, x in p.items()}, {k: 0 * x for k, x in p.items()}
    for step in range(1, STEPS + 1):
        hidden = np.tanh(z @ p["W1"] + p["b1"])
        c = np.tanh(hidden @ p["W2"] + p["b2"])
        pred = base * np.exp(c)
        err = np.clip((pred - y) / 0.03, -1, 1)                         # 크게 틀린 줄 하나에 끌려가지 않게 (Huber)
        d_out = err * pred * (1 - c * c) / len(y)
        d_hid = np.outer(d_out, p["W2"]) * (1 - hidden * hidden)
        grad = {"W1": z.T @ d_hid + 1e-4 * p["W1"], "b1": d_hid.sum(0), "W2": hidden.T @ d_out + 1e-4 * p["W2"], "b2": d_out.sum(keepdims=True)}
        for k in p:
            m[k] = 0.9 * m[k] + 0.1 * grad[k]
            v[k] = 0.999 * v[k] + 0.001 * grad[k] ** 2
            p[k] -= 0.01 * (m[k] / (1 - 0.9 ** step)) / (np.sqrt(v[k] / (1 - 0.999 ** step)) + 1e-8)
    return {"W1": p["W1"].tolist(), "b1": p["b1"].tolist(), "W2": p["W2"].tolist(), "b2": float(p["b2"][0])}


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("dataset")
    rows, sources = load(folder)
    plants = sorted({r[0] for r in rows})
    if len(rows) < 20 or len(plants) < 5:
        print(json.dumps({"ok": False, "error": f"{folder} 에서 쓸 수 있는 데이터가 너무 적어요 (구간 {len(rows)}개 · 화분 {len(plants)}개). "
                          "같은 화분을 여러 날 잰 CSV 가 필요해요 — 형식은 foresee/train.py 맨 위"}, ensure_ascii=False))
        sys.exit(1)
    who = np.array([r[0] for r in rows])
    temp, rh, dli, water, size, y = (np.array([r[i] for r in rows], float) for i in range(1, 7))
    ranges = {k: [float(v.min()), float(v.max())] for k, v in zip(COLS, (temp, rh, dli, water, size))}
    z, base = inputs(temp, rh, dli, water, size), formula(temp, rh, dli, size)
    rng = np.random.default_rng(0)

    def ensemble(of, n):
        """신경망 n 개: 화분을 (겹치게) 다시 뽑아 가며 하나씩 가르친다 — 그래서 답이 조금씩 다르고, 그 차이가 예측의 범위가 된다."""
        nets = []
        for k in range(n):
            idx = np.concatenate([np.nonzero(who == p)[0] for p in rng.choice(of, len(of))])
            nets.append(fit(z[idx], base[idx], y[idx], seed=k))
        return nets

    pred = np.empty(len(y))
    for fold in np.array_split(rng.permutation(plants), 5):             # 시험: 한 묶음씩 빼고 가르쳐서 그 묶음을 맞혀 본다
        test = np.isin(who, fold)
        nets = ensemble([p for p in plants if p not in set(fold)], 4)
        pred[test] = base[test] * np.mean([correction(z[test], net) for net in nets], 0)
    rmse = lambda p: round(float(np.sqrt(np.mean((p - y) ** 2))), 4)
    error = {"formula": rmse(base), "model": rmse(pred)}
    use = error["model"] < error["formula"]
    doc = {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "plant": PLANT, "use": use, "rows": len(rows), "plants": len(plants),
           "sources": sources, "error": error, "ranges": ranges, "nets": ensemble(plants, NETS)}       # 마지막에는 전부로 가르친다
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    MODEL.write_text(json.dumps(doc, ensure_ascii=False))
    flat = [k for k in COLS[:4] if ranges[k][0] == ranges[k][1]]
    text = (f"처음 보는 화분에서 하루 생장률 오차: 생장식 {error['formula']:.4f} → 학습한 모델 {error['model']:.4f}"
            + ("" if use else " — 나아지지 않아서 쓰지 않아요 (foresee 는 생장식으로 예측해요)"))
    print(json.dumps({"ok": True, "json": str(MODEL), "plant": PLANT, "use": use, "rows": len(rows), "plants": len(plants), "sources": sources,
                      "error": error, "no_variation": flat, "text": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
