#!/usr/bin/env python3
"""시험용 가상 재배 기록 (진짜 데이터가 아니다): 생장식과 일부러 다르게 자라는 화분 48개 → <폴더>/fake.csv

  다른 점: 25 °C 에서 멀어질수록 덜 자라고(생장식은 25~35 °C 가 같다), 물이 하루 45 mL 보다 적으면 덜 자란다.
  train.py 가 이 차이를 배워서 생장식보다 잘 맞히는지 보는 데 쓴다.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "foresee"))
from foresee import dli_of, formula

rng = np.random.default_rng(1)
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
lines = ["plant,day,size,temp_c,rh_pct,light_h,water_ml"]
for p in range(48):
    temp, rh, light, water = rng.uniform(16, 32), rng.uniform(40, 80), rng.choice([10, 12, 14, 16, 18]), rng.choice([15, 30, 45, 60])
    size = rng.uniform(2, 5)
    for day in range(25):
        if day % 4 == 0:
            lines.append(f"P{p:02d},{day},{size * rng.normal(1, 0.02):.2f},{temp + rng.normal(0, 0.3):.1f},{rh + rng.normal(0, 2):.0f},{light},{water}")
        differ = 1.3 * np.exp(-((temp - 25) / 8) ** 2) * (0.5 + 0.5 * min(1, water / 45))
        size *= np.exp(float(formula(temp, rh, dli_of(light), size)) * differ * rng.normal(1, 0.1))
(out / "fake.csv").write_text("\n".join(lines) + "\n")
print(f"{out / 'fake.csv'}  ({len(lines) - 1}줄)")
