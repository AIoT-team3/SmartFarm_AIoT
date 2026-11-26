"""급수 시점 예측 (foresee 의 일부): 마지막 급수 뒤 VPD 누적이 기준치에 닿는 때를 예측한다.

  누적      = Σ VPD(kPa) × 분 × 크기 계수                      (kPa·min)
  필요 시각 = 누적이 기준치(WATER_THRESHOLD)에 닿는 때
  앞으로의 VPD — B: 릿지 회귀(watering_model.json, watering_train.py 가 만든다)
                 A: 최근 3시간 평균과 어제 같은 시각을 반씩 (모델이 없거나 A 보다 못할 때)
docs/watering-forecast-design.md 3장.
"""
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, NamedTuple

import numpy as np

AHEAD_H = 12            # 모델이 내다보는 시간 (1~12시간 뒤를 한 시간씩)
LOOK_H = 72             # 필요 시각을 찾는 가장 먼 시간. 12시간 뒤부터는 24시간 전과 같다고 본다
GAP_MIN = 20            # 기록 사이가 이보다 길면 빈 구간 → 앞 3시간 평균으로 채운다
NO_DATA_H = 3           # 마지막 온습도가 이보다 오래되면 예측하지 않는다
MIN_GAP_H = 3           # 급수 사이 최소 간격
SIGMA = 0.15            # 모델이 없을 때 VPD 예측 오차 (kPa) — 필요 시각의 범위에 쓴다
REF_CM = 15.0           # 크기 계수 1 이 되는 키
SCORE_DAYS = 7          # 스스로 채점하는 기간
Z = 1.28                # 범위: 빠르면 · 늦으면 (10 % · 90 %)

HeightAt = Callable[[datetime], "float | None"]


class Reading(NamedTuple):
    ts: datetime
    vpd: float
    light: float
    temp: float
    rh: float


def vpd(temp: float, rh: float) -> float:
    """포차 (kPa)."""
    return 0.6108 * math.exp(17.27 * temp / (temp + 237.3)) * (1 - rh / 100)


def size_factor(height_cm: "float | None") -> float:
    """식물이 클수록 물을 빨리 쓴다: (키 / 15 cm) ^ 0.5, 0.7 ~ 1.5. 키를 모르면 1."""
    if height_cm is None or height_cm <= 0:
        return 1.0
    return float(np.clip((height_cm / REF_CM) ** 0.5, 0.7, 1.5))


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone()


def load_climate(path: Path) -> list[Reading]:
    """climate.jsonl → 온도 · 습도가 있는 줄만, 시각 순."""
    rows = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            r = json.loads(line)
            if not isinstance(r, dict) or r.get("temp_c") is None or r.get("rh_pct") is None:
                continue
            t, rh = float(r["temp_c"]), float(r["rh_pct"])
            if not (math.isfinite(t) and 0 <= rh <= 100):              # 센서가 NaN · 범위 밖 값을 낸 줄은 버린다
                continue
            rows.append(Reading(parse(r["ts"]), vpd(t, rh), float(r.get("light") or 0), t, rh))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return sorted(rows)


def last_watering(path: Path, now: datetime) -> "dict | None":
    """water.jsonl 에서 now 까지 실제로 화분에 준 마지막 물 (ml > 0, 유량 재기 by=test 는 빼고)."""
    last = None
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            r = json.loads(line)
            ml = float(r.get("ml") or 0)
            ts = parse(r["ts"])
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if not math.isfinite(ml):
            continue
        if r.get("by") == "test" or ml <= 0 or ts > now:
            continue
        if last is None or ts > last["ts"]:
            last = {"ts": ts, "ml": ml}
    return last


class Series:
    """온습도 기록을 시각으로 빨리 찾게 numpy 배열로."""

    def __init__(self, readings: list[Reading]):
        self.rows = readings
        self.t = np.array([r.ts.timestamp() for r in readings], float)
        self.v = np.array([r.vpd for r in readings], float)
        self.light = np.array([r.light for r in readings], float)
        self.temp = np.array([r.temp for r in readings], float)
        self.rh = np.array([r.rh for r in readings], float)

    def __len__(self):
        return len(self.rows)

    def mean(self, arr: np.ndarray, start: datetime, end: datetime) -> "float | None":
        """start < ts ≤ end 의 평균. 없으면 None."""
        i, j = np.searchsorted(self.t, [start.timestamp(), end.timestamp()], side="right")
        return float(arr[i:j].mean()) if j > i else None

    def accumulate(self, since: datetime, until: datetime, height_at: HeightAt) -> tuple[float, bool]:
        """since ~ until 의 누적 (kPa·min) → (누적, 빈 구간을 채웠는지). 각 기록의 VPD 는 다음 기록까지 이어진다고 본다."""
        i, j = np.searchsorted(self.t, [since.timestamp(), until.timestamp()], side="right")
        i = max(i - 1, 0)                                   # since 직전 기록부터 (since 이후 구간만 센다)
        total, filled = 0.0, False
        for k in range(i, j):
            a = self.rows[k]
            start = max(a.ts, since)
            end = self.rows[k + 1].ts if k + 1 < j else until
            minutes = (end - start).total_seconds() / 60
            if minutes <= 0:
                continue
            v = a.vpd
            if (end - a.ts).total_seconds() / 60 > GAP_MIN:
                m = self.mean(self.v, a.ts - timedelta(hours=3), a.ts)
                v, filled = (a.vpd if m is None else m), True
            total += v * minutes * size_factor(height_at(start))
        return total, filled


def features(s: Series, t0: datetime, h: int) -> "list[float] | None":
    """t0 에서 h 시간 뒤 한 시간의 VPD 를 맞히는 입력 9개. 최근 3시간 기록이 없으면 None."""
    at = t0 + timedelta(hours=h)
    now3 = s.mean(s.v, t0 - timedelta(hours=NO_DATA_H), t0)
    if now3 is None:
        return None
    recent = t0 - timedelta(hours=1)
    if s.mean(s.v, recent, t0) is None:                         # 최근 1시간이 비었으면 (센서가 잠깐 끊김) 3시간으로 본다
        recent = t0 - timedelta(hours=NO_DATA_H)
    now1, temp, rh = (s.mean(arr, recent, t0) for arr in (s.v, s.temp, s.rh))
    yday = s.mean(s.v, at - timedelta(hours=25), at - timedelta(hours=24))
    light = s.mean(s.light, at - timedelta(hours=25), at - timedelta(hours=24))     # 조명 시간표는 날마다 같다
    if yday is None:
        yday = now3
    if light is None:
        light = 1.0 if 6 <= at.hour < 22 else 0.0
    hour = at.hour + at.minute / 60
    return [1.0, math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24), light,
            now1, now3, yday, (temp - 22) / 6, (rh - 60) / 20]


def target(s: Series, t0: datetime, h: int) -> "float | None":
    """t0 에서 h 시간 뒤 한 시간의 실제 평균 VPD."""
    at = t0 + timedelta(hours=h)
    return s.mean(s.v, at - timedelta(hours=1), at)


def baseline(x: list[float]) -> float:
    """A: 최근 3시간 평균과 어제 같은 시각을 반씩."""
    return 0.5 * x[5] + 0.5 * x[6]


def ahead(s: Series, t0: datetime, model: "dict | None") -> "list[float] | None":
    """앞으로 LOOK_H 시간의 한 시간 평균 VPD (첫 12시간은 예측, 그 뒤는 24시간 전 값). 최근 기록이 없으면 None."""
    hourly = []
    for k in range(1, LOOK_H + 1):
        if k <= AHEAD_H:
            x = features(s, t0, k)
            if x is None:
                return None
            v = float(np.dot(model["weights"][k - 1], x)) if model else baseline(x)
        elif k - 24 >= 1:
            v = hourly[k - 25]
        else:
            at = t0 + timedelta(hours=k)
            past = s.mean(s.v, at - timedelta(hours=25), at - timedelta(hours=24))
            v = hourly[-1] if past is None else past
        hourly.append(max(v, 0.0))
    return hourly


def need_after(remaining: float, t0: datetime, hourly: list[float], height_at: HeightAt) -> "datetime | None":
    """t0 부터 remaining (kPa·min) 이 더 쌓이는 시각. LOOK_H 안에 안 닿으면 None."""
    if remaining <= 0:
        return t0
    total = 0.0
    for k, v in enumerate(hourly):
        rate = v * 60 * size_factor(height_at(t0 + timedelta(hours=k + 0.5)))
        if rate > 0 and total + rate >= remaining:
            return t0 + timedelta(hours=k + (remaining - total) / rate)
        total += rate
    return None


def shifted(hourly: list[float], sigma: list[float], sign: float) -> list[float]:
    return [max(v + sign * Z * sigma[min(k, AHEAD_H - 1)], 0.02) for k, v in enumerate(hourly)]


def load_model(path: "Path | None") -> tuple["dict | None", "str | None"]:
    """→ (모델 또는 None, 못 쓰는 까닭)."""
    if path is None or not Path(path).exists():
        return None, "급수 모델(watering_model.json)이 없어 최근 평균(A)으로 예측했어요"
    try:
        m = json.loads(Path(path).read_text())
        if not isinstance(m, dict):
            raise ValueError
        if not m.get("use"):
            return None, "급수 모델이 최근 평균(A)보다 낫지 않아서 쓰지 않았어요"
        number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
        weights, sigma = m["weights"], m["sigma"]
        if (len(weights) != AHEAD_H or len(sigma) != AHEAD_H        # 입력 개수가 바뀐 옛 모델 파일도 여기서 걸러진다
                or not all(isinstance(w, list) and len(w) == 9 and all(map(number, w)) for w in weights)
                or not all(number(x) and x >= 0 for x in sigma)):
            raise ValueError
        return m, None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, "급수 모델 파일(watering_model.json)을 읽지 못해 최근 평균(A)으로 예측했어요"


def actual_need(s: Series, t0: datetime, remaining: float, height_at: HeightAt) -> "datetime | None":
    """실제 기록으로 t0 부터 remaining 이 쌓인 시각 (기록 끝까지 안 닿으면 None)."""
    i = int(np.searchsorted(s.t, t0.timestamp(), side="right"))
    total = 0.0
    for k in range(i, len(s) - 1):
        a, b = s.rows[k], s.rows[k + 1]
        minutes = (b.ts - a.ts).total_seconds() / 60
        if minutes > GAP_MIN:
            return None                                         # 빈 구간이 있으면 채점하지 않는다
        rate = a.vpd * size_factor(height_at(a.ts))
        if total + rate * minutes >= remaining:
            return a.ts + timedelta(minutes=(remaining - total) / rate)
        total += rate * minutes
    return None


def score(s: Series, now: datetime, threshold: float, model: "dict | None", height_at: HeightAt) -> "dict | None":
    """지난 SCORE_DAYS 일의 정시마다: 그때 아는 것만으로 '기준치만큼 쌓이는 시각'을 예측했다면 몇 시간 틀렸나 (B 와 A)."""
    errors = {"model": [], "baseline": []}
    t = (now - timedelta(days=SCORE_DAYS)).replace(minute=0, second=0, microsecond=0)
    while t < now:
        real = actual_need(s, t, threshold, height_at)
        if real is not None:
            for kind, m in (("model", model), ("baseline", None)):
                if kind == "model" and model is None:
                    continue
                hourly = ahead(s, t, m)
                guess = hourly and need_after(threshold, t, hourly, height_at)
                if guess:
                    errors[kind].append(abs((guess - real).total_seconds()) / 3600)
        t += timedelta(hours=1)
    if not errors["baseline"]:
        return None
    base = round(float(np.mean(errors["baseline"])), 2)
    mine = round(float(np.mean(errors["model"])), 2) if errors["model"] else base
    return {"checks": len(errors["baseline"]), "error_h": mine, "baseline_error_h": base,
            "text": f"지난 {len(errors['baseline'])}번을 채점했더니 필요 시각이 평균 {mine:.1f}시간 틀렸어요 (최근 평균 A: {base:.1f}시간)"}


def iso(ts: "datetime | None") -> "str | None":
    return None if ts is None else ts.isoformat(timespec="seconds")


def forecast(data: Path, now: datetime, height_at: HeightAt, threshold: float = 1000.0, ml: float = 25.0,
             model_path: "Path | None" = None) -> dict:
    """foresee 가 부른다 → forecast.json 의 watering (설계 문서 4.1)."""
    s = Series([r for r in load_climate(data / "climate.jsonl") if r.ts <= now])
    model, why = load_model(model_path)
    notes = [why] if why else []
    last = last_watering(data / "water.jsonl", now)
    doc = {"made_at": iso(now), "last_water": last and {"ts": iso(last["ts"]), "ml": last["ml"]},
           "accum": None, "threshold": threshold, "unit": "kPa·min", "size_factor": round(size_factor(height_at(now)), 2),
           "need_at": None, "hours_left": None, "plan": None, "vpd_ahead": [], "method": "none", "skill": None, "notes": notes}
    if not len(s) or (now - s.rows[-1].ts).total_seconds() > NO_DATA_H * 3600:
        notes.append(f"온습도 기록이 {NO_DATA_H}시간 넘게 없어 급수 시점을 예측하지 않았어요")
        return doc
    since = last["ts"] if last else s.rows[0].ts
    if not last:
        notes.append("급수 기록이 없어 첫 온습도 기록부터 셌어요")
    accum, filled = s.accumulate(since, now, height_at)
    if filled:
        notes.append("온습도 기록이 빈 구간은 앞 3시간 평균으로 채웠어요")
    hourly = ahead(s, now, model)
    if hourly is None:
        notes.append("최근 1시간 온습도 기록이 없어 급수 시점을 예측하지 않았어요")
        doc["accum"] = round(accum, 1)
        return doc
    sigma = model["sigma"] if model else [SIGMA] * AHEAD_H
    remaining = threshold - accum
    mid = need_after(remaining, now, hourly, height_at)
    early = need_after(remaining, now, shifted(hourly, sigma, +1), height_at)
    late = need_after(remaining, now, shifted(hourly, sigma, -1), height_at)
    doc.update(accum=round(accum, 1), method="model" if model else "baseline",
               need_at={"early": iso(early), "mid": iso(mid), "late": iso(late)},
               vpd_ahead=[{"ts": iso(now + timedelta(hours=k + 1)), "vpd_kpa": round(v, 3)} for k, v in enumerate(hourly[:AHEAD_H])],
               skill=score(s, now, threshold, model, height_at))
    if mid is None:
        notes.append(f"앞으로 {LOOK_H}시간 안에는 물이 필요하지 않아요")
        return doc
    water_at = max(mid, last["ts"] + timedelta(hours=MIN_GAP_H)) if last else mid
    doc["hours_left"] = round((mid - now).total_seconds() / 3600, 1)
    doc["plan"] = {"water_at": iso(water_at), "ml": ml}
    return doc
