#!/usr/bin/env python3
"""시험용 가상 기록 (진짜가 아니다): 파를 날마다 한 번씩 스캔한 것처럼 scans/index.json · climate.jsonl 을 만든다.

  fakehistory.py <폴더> [스캔 수=8] [하루에 자라는 cm=1.3]      → "SOWN=<심은 날>" 을 찍는다

예측이 스캔에서 배우고(personal) 스스로 채점하는지(skill) 볼 때, 대시보드에 곡선을 먼저 그려 볼 때 쓴다:
  env $(python3 test/fakehistory.py /tmp/h) DATA=/tmp/h PLANT=greenonion python3 foresee/foresee.py
가상 파는 예측 모델의 식과 다르게 자란다 (하루 몇 cm 씩 꾸준히 · 따뜻한 날 조금 더 · 클수록 조금 덜 · 스캔 오차 0.3 cm).
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

out, n, speed = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 8, float(sys.argv[3]) if len(sys.argv) > 3 else 1.3
rng = np.random.default_rng(5)
now = datetime.now().astimezone()
last = now.replace(hour=12, minute=0, second=0, microsecond=0)         # 마지막 스캔: 오늘 낮 12시 (아직 12시 전이면 어제)
if last > now:
    last -= timedelta(days=1)
first = last - timedelta(days=n - 1)
sown = (first - timedelta(days=2)).replace(hour=0)                      # 첫 스캔은 심은 뒤 2일째
temp = 22 + 2.5 * np.sin(np.arange(n + 2) / 2) + rng.normal(0, 0.5, n + 2)
height, scans = 3.0, []
for i in range(n):
    ts = first + timedelta(days=i)
    scans.append({"scan_id": ts.strftime("%Y%m%d-%H%M%S"), "ts": ts.isoformat(timespec="seconds"),
                  "day": round((ts - sown).total_seconds() / 86400, 2), "height_cm": round(height + rng.normal(0, 0.3), 1)})
    height += speed * (1 + 0.03 * (temp[i] - 22)) * (1 - height / 45) * rng.normal(1, 0.08)
(out / "scans").mkdir(parents=True, exist_ok=True)
(out / "scans" / "index.json").write_text(json.dumps(scans, ensure_ascii=False, indent=1))
rows, t = [], first.replace(hour=0)
while t < now:
    rows.append(json.dumps({"ts": t.isoformat(timespec="seconds"), "temp_c": round(float(temp[(t - first.replace(hour=0)).days]), 1),
                            "rh_pct": 60.0, "light": int(6 <= t.hour < 22), "fan": 0}))
    t += timedelta(minutes=10)
(out / "climate.jsonl").write_text("\n".join(rows) + "\n")
print(f"SOWN={sown:%Y-%m-%d}")
