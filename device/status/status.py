#!/usr/bin/env python3
"""지금 상태 한 장: Pi ① 의 상태 + 마지막 스캔 + 예측을 읽어, 판정(좋음 · 주의 · 경고)과 한 문장까지 붙인다.

대시보드의 '지금' 화면은 status.json 을 계산 없이 그리기만 하면 된다. 아무것도 움직이지 않는다(읽기만) —
상태를 보고 조명 · 팬 · 급수를 움직이는 일은 Pi ① 이 2초마다 스스로 하고(pi/pi.py), 무엇을 왜 했는지가 actions 에 온다.
"""
import json
import math
import os
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

E = os.environ.get
PI1, PI2 = E("PI1", "http://localhost:8765"), E("PI2", "http://localhost:8766")
DATA = Path(E("DATA", "data")).resolve()

# 실내 재배 기준: (좋음 범위, 주의 범위) — 그 밖은 경고. 기온은 식물(config.env 의 PLANT)마다 다르다
TEMP = {"basil": ((18, 29), (15, 32)), "greenonion": ((15, 27), (8, 31))}.get(os.environ.get("PLANT", "basil"), ((18, 29), (15, 32)))
RANGES = {"temp_c": ("기온", "°C", *TEMP),
          "rh_pct": ("습도", "%", (40, 70), (30, 80)),
          "vpd_kpa": ("포차", "kPa", (0.5, 1.5), (0.3, 1.8))}
LABEL = {"good": "좋음", "info": "알림", "warning": "주의", "serious": "경고", "critical": "위험"}
ORDER = ["critical", "serious", "warning", "info", "good"]


def get(url):
    try:
        with urlopen(url + "/state", timeout=5) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def read(path):
    try:
        return json.loads((DATA / path).read_text())
    except (OSError, ValueError):
        return None


def main():
    now = datetime.now().astimezone()
    pi1, pi2 = get(PI1), get(PI2)
    alerts = []

    def alert(level, code, text):
        alerts.append({"level": level, "level_label": LABEL[level], "code": code, "text": text})

    if not pi1:
        alert("serious", "pi1_down", f"Pi ①({PI1})이 응답하지 않아요 — 전원 · Wi-Fi 를 확인하세요")
    if not pi2:
        alert("warning", "pi2_down", f"Pi ②({PI2})가 응답하지 않아요 — 스캔은 아래 카메라 한 대로만 해요")
    light, fan, water, climate = [(pi1 or {}).get(k) for k in ("light", "fan", "water", "climate")]

    # 환경: 기온 · 습도 · 포차(VPD)
    metrics = []
    if climate and climate.get("temp_c") is not None:
        t, rh = climate["temp_c"], climate["rh_pct"]
        vpd = round(0.6108 * math.exp(17.27 * t / (t + 237.3)) * (1 - rh / 100), 2)
        for key, value in (("temp_c", t), ("rh_pct", rh), ("vpd_kpa", vpd)):
            label, unit, good, warn = RANGES[key]
            level = "good" if good[0] <= value <= good[1] else "warning" if warn[0] <= value <= warn[1] else "serious"
            side = "낮아요" if value < good[0] else "높아요"
            text = "좋아요" if level == "good" else f"{label}{'이' if key == 'temp_c' else '가'} {side} ({value:g} {unit}, 적정 {good[0]:g}~{good[1]:g})"
            metrics.append({"key": key, "label": label, "value": value, "unit": unit, "level": level,
                            "level_label": LABEL[level], "text": text})
            if level != "good":
                alert(level, key, text)
    elif pi1:
        alert("info", "no_sensor", f"온습도 값이 없어요 ({(climate or {}).get('error') or '센서 확인'})")

    # 급수
    db = (pi1 or {}).get("db")
    if water:
        if water["stopped"]:
            alert("critical", "stopped", "비상정지 중이에요 — 급수가 막혀 있어요 (water.sh resume)")
        if not water["tank_ok"]:
            alert("serious", "tank_empty", "물통이 비었어요")
        if not water["flow_ml_s"]:
            alert("info", "no_flow", "펌프 유량을 아직 안 쟀어요 (water.sh sec 3 → water.sh flow <mL/초>)")
        elif not water["times"] and not (db and db.get("url")):     # 서버에 이어져 있으면 서버의 급수 예측이 물을 준다
            alert("info", "no_schedule", "자동 급수가 꺼져 있어요 — 서버 없이 돌린다면 water.sh schedule 09:00 25")
    if db and db.get("url") and db.get("error"):
        alert("info", "db", f"DB 로 올리지 못하고 있어요 — {db['error']}")

    # 생장: 마지막 스캔 · 7일 변화 · 수확 예상
    scans = [r for r in (read("scans/index.json") or []) if r.get("height_cm")]
    forecast = read("foresee/forecast.json")
    harvest = (forecast or {}).get("harvest")
    growth = {"scans": len(scans), "last": scans[-1] if scans else None, "delta_7d_cm": None, "harvest": harvest}
    if scans:
        t_last = datetime.fromisoformat(scans[-1]["ts"])
        week = [r for r in scans if 5 <= (t_last - datetime.fromisoformat(r["ts"])).total_seconds() / 86400 <= 9]
        if week:
            growth["delta_7d_cm"] = round(scans[-1]["height_cm"] - week[-1]["height_cm"], 1)

    # 한 문장
    alerts.sort(key=lambda a: ORDER.index(a["level"]))
    top = alerts[0] if alerts and alerts[0]["level"] in ("critical", "serious", "warning") else None
    if top:
        headline = {"level": top["level"], "title": top["text"], "detail": ""}
    elif not scans:
        headline = {"level": "info", "title": "첫 스캔을 기다리고 있어요", "detail": "camera.sh 로 스캔하면 키와 3D 가 나와요"}
    elif harvest and harvest.get("ready"):
        headline = {"level": "good", "title": "수확할 때가 됐어요", "detail": f"키 {scans[-1]['height_cm']:g} cm"}
    else:
        left = harvest and harvest.get("days_left")
        headline = {"level": "good", "title": "잘 자라고 있어요",
                    "detail": f"키 {scans[-1]['height_cm']:g} cm" + (f" · 수확까지 약 {left}일" if left is not None else "")}
    headline["level_label"] = LABEL[headline["level"]]

    doc = {"made_at": now.isoformat(timespec="seconds"), "headline": headline, "alerts": alerts,
           "env": {"ts": (climate or {}).get("ts"), "metrics": metrics}, "growth": growth,
           "water": water, "light": light, "fan": fan,
           "actions": (pi1 or {}).get("actions") or [],         # Pi ① 이 스스로 판단해서 한 일 (최근 10개)
           "db": db,
           "devices": [{"name": "Pi ①", "url": PI1, "ok": bool(pi1)}, {"name": "Pi ②", "url": PI2, "ok": bool(pi2)}]}
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "status.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    print(json.dumps({"ok": True, "json": str(DATA / "status.json"), "headline": headline, "alerts": len(alerts)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
