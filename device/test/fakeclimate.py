#!/usr/bin/env python3
"""시험용 가상 온습도 · 급수 기록 (진짜가 아니다): 낮밤 · 조명 · 날마다 다른 날씨가 있는 climate.jsonl 과 water.jsonl.

  fakeclimate.py <폴더> [날 수=10]        → 지금까지 10분마다 한 줄. 급수는 VPD 누적 1000 마다 25 mL

급수 예측(watering.py)과 학습(watering_train.py)을 부품 없이 시험할 때 쓴다.
"""
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

out, days = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 10
rng = np.random.default_rng(7)
now = datetime.now().astimezone().replace(second=0, microsecond=0)
t = (now - timedelta(days=days)).replace(minute=0)
weather = 0.0                                                       # 날마다 조금씩 바뀌는 날씨 (따뜻 · 건조한 정도)
climate, water, accum = [], [], 0.0
while t <= now:
    if t.hour == 0 and t.minute == 0:
        weather = 0.7 * weather + rng.normal(0, 1)
    light = int(6 <= t.hour < 22)
    day = math.sin(2 * math.pi * (t.hour + t.minute / 60 - 9) / 24)
    temp = 21 + 2.5 * day + 1.5 * light + 1.2 * weather + rng.normal(0, 0.3)
    rh = float(np.clip(62 - 8 * day - 4 * light - 4 * weather + rng.normal(0, 1.0), 20, 95))
    climate.append({"ts": t.isoformat(timespec="seconds"), "temp_c": round(temp, 1), "rh_pct": round(rh, 1), "light": light, "fan": 0})
    accum += 0.6108 * math.exp(17.27 * temp / (temp + 237.3)) * (1 - rh / 100) * 10
    if accum >= 1000:
        water.append({"ts": t.isoformat(timespec="seconds"), "ml": 25.0, "sec": 1.8, "by": "auto"})
        accum = 0.0
    t += timedelta(minutes=10)
out.mkdir(parents=True, exist_ok=True)
(out / "climate.jsonl").write_text("\n".join(json.dumps(r) for r in climate) + "\n")
(out / "water.jsonl").write_text("\n".join(json.dumps(r) for r in water) + "\n")
print(json.dumps({"ok": True, "climate": len(climate), "water": len(water)}))
