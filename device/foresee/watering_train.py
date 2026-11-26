#!/usr/bin/env python3
"""급수 모델 학습: 온습도 기록(climate.jsonl) → 1~12시간 뒤 VPD 를 맞히는 릿지 회귀 12개 → foresee/watering_model.json  (맥북에서, 몇 초)

  watering_train.sh [climate.jsonl 이 든 폴더]       기본 폴더는 foresee/dataset/ — 그 안의 *.jsonl 을 모두 쓴다

시험: 파일마다 마지막 20 % 시간을 떼어 두고 앞 80 % 로 가르친 뒤 맞혀 본다.
최근 평균(A)보다 못 맞히면 "use": false — foresee 는 A 로 예측한다.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from watering import AHEAD_H, Series, baseline, features, load_climate, target

E = os.environ.get
OUT = Path(E("WATER_MODEL", str(Path(__file__).with_name("watering_model.json"))))
LAMBDA = 1.0
MIN_DAYS = 3


def samples(s: Series, start: datetime, end: datetime) -> list[list[tuple]]:
    """정시마다 (입력, 정답) — 시간(h)마다 따로."""
    rows = [[] for _ in range(AHEAD_H)]
    t = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while t + timedelta(hours=AHEAD_H) <= end:
        for h in range(1, AHEAD_H + 1):
            x, y = features(s, t, h), target(s, t, h)
            if x is not None and y is not None:
                rows[h - 1].append((x, y))
        t += timedelta(hours=1)
    return rows


def ridge(rows: list[tuple]) -> np.ndarray:
    X = np.array([x for x, _ in rows], float)
    y = np.array([y for _, y in rows], float)
    return np.linalg.solve(X.T @ X + LAMBDA * np.eye(X.shape[1]), X.T @ y)


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("dataset")
    files = sorted(folder.glob("*.jsonl"))
    series = [Series(load_climate(f)) for f in files]
    series = [s for s in series if len(s) and (s.rows[-1].ts - s.rows[0].ts).days >= MIN_DAYS]
    if not series:
        print(json.dumps({"ok": False, "error": f"{folder} 에 {MIN_DAYS}일 넘게 이어진 온습도 기록(*.jsonl)이 없어요 — Pi 의 data/climate.jsonl 을 복사해 넣으세요"},
                         ensure_ascii=False))
        sys.exit(1)
    train, test, full = ([[] for _ in range(AHEAD_H)] for _ in range(3))
    for s in series:
        first, last = s.rows[0].ts + timedelta(hours=25), s.rows[-1].ts
        cut = first + (last - first) * 0.8
        for h, (a, b, c) in enumerate(zip(samples(s, first, cut), samples(s, cut, last), samples(s, first, last))):
            train[h] += a
            test[h] += b
            full[h] += c
    if min(len(r) for r in test) < 10:
        print(json.dumps({"ok": False, "error": "시험할 기록이 너무 적어요 — 며칠 더 모은 뒤 다시 돌리세요"}, ensure_ascii=False))
        sys.exit(1)
    err_model, err_base, sigma = [], [], []
    for h in range(AHEAD_H):
        w = ridge(train[h])
        res = np.array([float(np.dot(w, x)) - y for x, y in test[h]])
        base = np.array([baseline(x) - y for x, y in test[h]])
        err_model.append(float(np.sqrt(np.mean(res ** 2))))
        err_base.append(float(np.sqrt(np.mean(base ** 2))))
        sigma.append(round(float(np.std(res)), 4))
    error = {"model": round(float(np.mean(err_model)), 4), "baseline": round(float(np.mean(err_base)), 4)}
    use = error["model"] < error["baseline"]
    doc = {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "use": use,
           "rows": sum(len(r) for r in full), "files": [f.name for f in files], "error": error, "sigma": sigma,
           "weights": [ridge(full[h]).round(5).tolist() for h in range(AHEAD_H)]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False))
    text = (f"1~12시간 뒤 VPD 오차: 최근 평균(A) {error['baseline']:.3f} kPa → 학습한 모델 {error['model']:.3f} kPa"
            + ("" if use else " — 나아지지 않아서 쓰지 않아요 (foresee 는 A 로 예측해요)"))
    print(json.dumps({"ok": True, "json": str(OUT), "use": use, "rows": doc["rows"], "error": error, "text": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
