#!/usr/bin/env python3
"""GreenTwin — 라즈베리파이에서 도는 것 전부 (이 파일 하나).

  맥북이 HTTP 로 시키면 한다   사진 찍기 · 회전판 돌리기 · 조명/팬/펌프 켜고 끄기 · 온습도 읽기
  혼자서도 한다 (Pi ① 만)      2초마다 지금 상태를 보고: 조명 시간표 · 더우면 조명 끄기 · 습하거나 더우면 팬 ·
                              정해 둔 시각의 급수(쓸 때만) · 24시간 동안 급수가 없으면 비상 급수 ·
                              덥고 건조하면 물 한 번 더(DRY_EXTRA=1 일 때만).  10분마다: 온습도 기록
                              평소의 급수 시점은 서버의 급수 예측(hub)이 water 명령으로 정한다 (docs/watering-forecast-design.md)
  서버와 잇기 (pi/link.py)   온습도는 MQTT, 급수 · 조명 · 팬 · 스캔은 HTTP 로 팀 서버에 올리고, 대시보드 명령을 받아 한다
  → 맥북이 꺼져 있어도 화분은 돌봄을 받고, 기록은 DB 에 쌓인다

  Pi ① (PI=1): 회전판 · 릴레이 3개 · 온습도 · 아래 카메라        Pi ② (PI=2): 가운데 카메라만
  실행  python3 pi/pi.py      설정  pi/pi.env      확인  curl http://localhost:8765/state
"""
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import link                             # pi/link.py — 팀 서버(FastAPI · MQTT)와 잇는 부분

E = os.environ.get
PI, PORT = int(E("PI", "1")), int(E("PORT", "8765"))
DATA = Path(E("DATA", str(Path.home() / "greentwin-data")))     # 상태 · 기록 파일을 두는 곳
SENSOR, SENSOR_CMD = E("SENSOR", "dht"), E("SENSOR_CMD", "")    # dht | cmd | none
RELAY_ON = int(E("RELAY_ON", "0"))      # 릴레이가 켜지는 레벨. 0 = IN 이 LOW 일 때 켜지는 모듈
HOME_ON = int(E("HOME_ON", "0"))        # 원점 센서가 자석 위에서 내는 레벨
TURN_DIR = int(E("TURN_DIR", "1"))      # 회전판이 반대로 돌면 -1
DB_URL, PLANT_ID = E("DB_URL", "").rstrip("/"), int(E("PLANT_ID", "1"))        # 기록을 올릴 DB 서버 (비우면 안 올린다)
TOKEN_FILE = Path(E("DB_TOKEN_FILE", str(Path(__file__).resolve().parent.parent / "db" / "token")))

# 핀 (BCM 번호) — docs/wiring.html 과 같다
COILS = [20, 21, 19, 6]                 # ULN2003 IN1~IN4
PIN_HOME, PIN_TANK = 26, 27             # 원점 센서(리드 스위치) · 물통(GND 로 잇는 점퍼선 = 물 있음)
RELAYS = {"light": 12, "fan": 5, "pump": 17}
STEPS = 4076                            # 28BYJ-48 하프스텝으로 회전판 한 바퀴
STEP_S = 0.005                          # 한 스텝에 걸리는 시간 (느리게 = 힘이 세게). 실물에서 0.003 은 스텝을 놓쳤다
CLEAR = 450                             # 자석 구역을 이만큼(40°) 벗어났다가 다시 만나야 원점으로 친다
MAX_DOSE_ML, MAX_DAY_ML = 60, 300       # 물: 한 번 최대 · 하루 최대

# 스스로 판단하는 기준 (auto 일 때)
FAN_RH, FAN_T = 70, 28                  # 습도 · 기온이 이 이상이면 팬을 켠다 (각각 65 % · 27 °C 아래로 내려가면 끈다)
HEAT_OFF, HEAT_BACK = 32, 30            # 이 기온이면 조명을 끄고, 이 아래로 식으면 다시 시간표대로
DRY_T, DRY_RH = 30, 40                  # 이만큼 덥고 건조하면 물을 한 번 더 준다 (하루 한 번, 마지막 급수 뒤 3시간이 지났을 때만)
DRY_EXTRA_DEFAULT = False               # 급수 시점은 서버의 급수 예측(hub)이 정한다 — 겹치지 않게 '한 번 더' 는 기본으로 끈다
DRY_EXTRA = E("DRY_EXTRA", "1" if DRY_EXTRA_DEFAULT else "0") == "1"     # 서버 없이 혼자 돌릴 때는 pi.env 에 DRY_EXTRA=1
FALLBACK_H = 24                         # 이만큼 물을 한 번도 안 줬으면 (서버가 멈췄을 때) Pi 가 한 번 준다

# 맥북에서 바꾸는 설정 (state.json 에 저장되어 다시 켜도 남는다)
S = {"light": "auto", "start": "06:00", "hours": 16.0, "fan": "auto", "times": [], "each": 25.0, "flow": 0.0,
     "stopped": False, "day": "", "today_ml": 0.0, "done": [], "last": None}
ON = {"light": False, "fan": False, "pump": False}                           # 지금 릴레이 상태
C = {"temp_c": None, "rh_pct": None, "ts": None, "error": "아직 안 읽었어요"}   # 마지막 온습도
WHY = {"light": "", "fan": ""}          # 지금 그 상태인 까닭 (화면에 그대로 보여 줄 문장)
KIND = {"light": "", "fan": ""}
LINK = None         # 서버 연결 (pi/link.py) — DB_URL 이 있을 때만
HOLD = 0.0          # 이 시각까지는 스캔 중: 조명을 켜고 팬을 끈다
HOT = False         # 더워서 조명을 꺼 둔 상태인가
POS = None          # 회전판이 원점에서 몇 스텝 돌았나 (None = 원점을 아직 안 찾음)
AWAY = 0            # 원점 센서가 꺼진 채로 이어서 돈 스텝 수 (자석 구역을 얼마나 벗어나 있나)
PHASE = 0
CAM = None
LOCK = {k: threading.Lock() for k in ("tick", "motor", "pump", "cam", "db", "save")}
READ_AT = LOGGED_AT = 0.0


class Oops(Exception):
    """맥북에 그대로 돌려줄 오류 문장."""


def now():
    return datetime.now().astimezone()


def iso(t=None):
    return (t or now()).isoformat(timespec="seconds")


def save():
    with LOCK["save"]:                  # 자동 운전 · 맥북 명령 · DB 올리기가 동시에 저장해도 파일이 깨지지 않게
        tmp = DATA / "state.json.tmp"
        tmp.write_text(json.dumps(S, ensure_ascii=False))
        tmp.replace(DATA / "state.json")


def log(name, row):
    with open(DATA / f"{name}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def did(what, text):
    """스스로 판단해서 한 일을 남긴다 (actions.jsonl) — 대시보드가 '무엇을 왜 했는지' 보여 줄 수 있게."""
    log("actions", {"ts": iso(), "what": what, "text": text})


# ───────────── 릴레이 · 물통 ─────────────
def relay(name, on):
    if ON[name] != on:
        lgpio.gpio_write(H, RELAYS[name], RELAY_ON if on else 1 - RELAY_ON)
        ON[name] = on
        if name != "pump":                                      # 조명 · 팬을 켜고 끈 기록 (서버로 올라간다). 펌프는 water.jsonl
            log("switch", {"ts": iso(), "part": name, "on": on, "kind": KIND[name], "why": WHY[name]})


def tank_ok():
    return lgpio.gpio_read(H, PIN_TANK) == 0


def water(ml=None, sec=None, by="manual"):
    """펌프를 돌린다. ml 은 유량(S['flow'])으로 초로 바꾸고, sec 는 유량을 잴 때 쓴다(최대 5초)."""
    if S["stopped"]:
        raise Oops("비상정지 중이에요 — water.sh resume 으로 풀어 주세요")
    if not tank_ok():
        raise Oops("물통이 비었어요 (GPIO 27 ↔ GND 점퍼선이 꽂혀 있는지 보세요)")
    if sec is None:
        if not S["flow"]:
            raise Oops("펌프 유량을 아직 안 쟀어요 — water.sh sec 3 으로 컵에 받아 보고, water.sh flow <1초에 나온 mL>")
        ml = min(float(ml), MAX_DOSE_ML)
        if S["today_ml"] + ml > MAX_DAY_ML:
            raise Oops(f"오늘은 {max(0, MAX_DAY_ML - S['today_ml']):g} mL 까지만 더 줄 수 있어요 (하루 {MAX_DAY_ML} mL)")
        sec = ml / S["flow"]
    else:
        sec = min(float(sec), 5.0)
    if sec <= 0:
        raise Oops("양은 0 보다 커야 해요")
    if not LOCK["pump"].acquire(blocking=False):
        raise Oops("펌프가 이미 돌고 있어요")
    t0 = time.time()
    try:
        relay("fan", False)
        relay("pump", True)
        while time.time() - t0 < sec and not S["stopped"]:      # 비상정지를 누르면 바로 멈춘다
            time.sleep(0.02)
    finally:
        relay("pump", False)
        ran = time.time() - t0
        LOCK["pump"].release()
    S["last"] = {"ts": iso(), "ml": round(ran * S["flow"], 1), "sec": round(ran, 2), "by": by}
    if by != "test":                                            # 유량을 재느라 컵에 받은 물은 화분에 준 것이 아니다
        S["last_given"] = S["last"]["ts"]
    S["today_ml"] = round(S["today_ml"] + S["last"]["ml"], 1)
    save()
    log("water", S["last"])
    return S["last"]


# ───────────── 회전판 (28BYJ-48 + ULN2003, 늘 한 방향으로만 돈다) ─────────────
SEQ = (1, 3, 2, 6, 4, 12, 8, 9)         # 하프스텝 순서 (bit0 = IN1)


def at_home():
    return lgpio.gpio_read(H, PIN_HOME) == HOME_ON


def turn(n, until=None, delay=STEP_S):
    """n 스텝 돈다. until(센서가 켜졌나, 그 전까지 꺼진 채로 돈 스텝 수) 이 참이 되면 그 자리에서 멈춘다 → 실제로 간 스텝 수."""
    global PHASE, AWAY
    done = 0
    while done < n:
        PHASE = (PHASE + TURN_DIR) % 8
        lgpio.group_write(H, COILS[0], SEQ[PHASE])
        # 처음과 끝 80스텝(약 7°)은 천천히: 무거운 화분을 갑자기 밀면 모터가 헛돈다
        time.sleep(delay + 0.007 * max(0.0, 1 - min(done, n - 1 - done) / 80))
        done += 1
        on = at_home()
        away, AWAY = AWAY, 0 if on else AWAY + 1
        if until and until(on, away):
            break
    time.sleep(0.05)
    lgpio.group_write(H, COILS[0], 0)   # 코일 전류를 끈다 (감속기가 회전판을 잡고 있다)
    return done


def home():
    """원점 찾기 → 거기까지 돈 스텝 수.

    원점 = 센서가 40° 넘게 꺼져 있다가 처음 켜지는 자리. 리드 스위치는 자석 하나에 두세 번 깜빡일 수 있고
    그 사이에 서 있다가 출발할 수도 있어서, '충분히 벗어났다가 다시 만난 맨 앞 끝'만 원점으로 친다.
    그래서 자석 위나 근처에서 출발하면 한 바퀴를 돌아서 온다."""
    global POS
    n = STEPS + 1200
    moved = turn(n, lambda on, away: on and away >= CLEAR, delay=STEP_S + 0.001)
    if moved >= n:
        raise Oops("원점 센서가 계속 켜져 있어요 — 자석이 멀리 있는데도 그렇다면 pi/pi.env 의 HOME_ON 을 1 로 바꾸세요. "
                   "아니면 회전판이 실제로 도는지, 센서와 자석 자리를 보세요" if at_home() else
                   "한 바퀴를 돌려도 원점 센서가 안 켜져요 — 회전판이 실제로 도는지, 자석과 센서 사이 틈, 배선(DO → GPIO 26)을 보세요")
    POS = 0
    return moved


def goto(deg):
    global POS
    if POS is None:
        home()
    target = round(deg % 360 / 360 * STEPS)
    moved = turn((target - POS) % STEPS)
    POS = (POS + moved) % STEPS
    return moved


# ───────────── 카메라 · 온습도 ─────────────
def photo():
    global CAM
    with LOCK["cam"]:
        if CAM is None:
            cam = None
            try:
                from picamera2 import Picamera2
                cam = Picamera2()
                cam.configure(cam.create_still_configuration(main={"size": (1296, 972)}))
                cam.start()
            except Exception as e:      # 카메라가 안 보인다 (리본이 덜 꽂혔거나 뒤집혔을 때가 가장 많다)
                if cam is not None:
                    try:
                        cam.close()
                    except Exception:
                        pass
                raise Oops(f"카메라가 안 켜져요 — Pi 를 끄고 리본 케이블이 끝까지, 맞는 방향으로 꽂혔는지 보세요 ({type(e).__name__}: {e})")
            time.sleep(2)               # 자동 노출 · 화이트밸런스가 자리 잡을 때까지
            CAM = cam
        buf = io.BytesIO()
        CAM.capture_file(buf, format="jpeg")
        return buf.getvalue()


def read_sensor():
    """온습도를 읽어 C 에 넣는다. 못 읽으면 C['error'] 에 이유."""
    try:
        if SENSOR == "dht":             # DHT11 · DHT22: 커널 드라이버(dtoverlay=dht11,gpiopin=4)가 읽어 준 값
            found = list(Path("/sys/bus/iio/devices").glob("iio:device*/in_temp_input"))
            if not found:
                raise OSError("DHT 드라이버가 안 보여요 — bash pi/install.sh 1 뒤에 다시 켰는지 보세요")
            for _ in range(6):          # DHT 는 원래 몇 번에 한 번은 못 읽는다
                try:
                    t = int(found[0].read_text()) / 1000
                    h = int((found[0].parent / "in_humidityrelative_input").read_text()) / 1000
                    break
                except OSError:
                    time.sleep(1)
            else:
                raise OSError("센서가 답하지 않아요 — 배선(DATA → GPIO 4, 3.3 V, GND)을 보세요")
        elif SENSOR == "cmd":           # 다른 센서: {"temp_c": 23.4, "rh_pct": 61} 한 줄을 찍는 명령
            out = subprocess.run(SENSOR_CMD, shell=True, capture_output=True, text=True, timeout=15).stdout
            d = json.loads(out.strip().splitlines()[-1])
            t, h = float(d["temp_c"]), float(d["rh_pct"])
        else:
            raise OSError("온습도 센서 없음 (pi.env 의 SENSOR)")
        C.update(temp_c=round(t, 1), rh_pct=round(h, 1), ts=iso(), error=None)
    except Exception as e:
        C.update(temp_c=None, rh_pct=None, error=str(e) or type(e).__name__)


# ───────────── 자동 운전 (2초마다) ─────────────
def light_window(t):
    """조명 시간표 안인가 (자정을 넘는 시간표도 된다)."""
    h, m = map(int, S["start"].split(":"))
    start = t.replace(hour=h, minute=m, second=0, microsecond=0)
    return any(s <= t < s + timedelta(hours=S["hours"]) for s in (start, start - timedelta(days=1)))


def tick():
    """지금 상태를 보고 조명 · 팬 · 급수를 정한다."""
    global LOGGED_AT, HOT
    with LOCK["tick"]:
        t = now()
        if S["day"] != t.strftime("%Y-%m-%d"):                  # 날이 바뀌면 오늘 준 물을 0 으로
            S.update(day=t.strftime("%Y-%m-%d"), today_ml=0.0, done=[])
            save()
        T, RH = C["temp_c"], C["rh_pct"]
        hold, was, before = time.time() < HOLD, ON["light"], dict(KIND)

        HOT = T is not None and T >= (HEAT_BACK if HOT else HEAT_OFF)
        if hold:
            light, KIND["light"], WHY["light"] = True, "scan", "스캔하는 중이라 켰어요"
        elif S["light"] != "auto":
            light, KIND["light"], WHY["light"] = S["light"] == "on", "manual", "직접 켜 둠" if S["light"] == "on" else "직접 꺼 둠"
        elif HOT:
            light, KIND["light"], WHY["light"] = False, "heat", f"더워서 껐어요 ({T:g} °C)"
        else:
            light, KIND["light"], WHY["light"] = light_window(t), "timetable", f"시간표 ({S['start']} 부터 {S['hours']:g}시간)"
        relay("light", light)

        humid = RH is not None and RH >= (FAN_RH - 5 if KIND["fan"] == "humid" else FAN_RH)
        hot = T is not None and T >= (FAN_T - 1 if KIND["fan"] == "hot" else FAN_T)
        if hold:
            fan, KIND["fan"], WHY["fan"] = False, "scan", "스캔하는 중이라 껐어요"
        elif ON["pump"]:
            fan, KIND["fan"], WHY["fan"] = False, "pump", "물 주는 중이라 껐어요"
        elif S["fan"] != "auto":
            fan, KIND["fan"], WHY["fan"] = S["fan"] == "on", "manual", "직접 켜 둠" if S["fan"] == "on" else "직접 꺼 둠"
        elif humid:
            fan, KIND["fan"], WHY["fan"] = True, "humid", f"습해서 켰어요 ({RH:g} %)"
        elif hot:
            fan, KIND["fan"], WHY["fan"] = True, "hot", f"더워서 켰어요 ({T:g} °C)"
        elif t.minute < 10 and not (T is not None and T < 18):
            fan, KIND["fan"], WHY["fan"] = True, "hourly", "정기 환기 (매시 10분)"
        else:
            fan, KIND["fan"], WHY["fan"] = False, "idle", "쉬는 중"
        relay("fan", fan)

        for part in ("light", "fan"):                           # 평소와 다르게 판단한 일만 남긴다
            if KIND[part] != before[part] and KIND[part] in ("heat", "humid", "hot"):
                did(part, ("조명: " if part == "light" else "팬: ") + WHY[part])
            elif before[part] == "heat" and KIND[part] != "heat":
                did(part, "조명: 식어서 다시 시간표대로 해요")
        if time.time() - LOGGED_AT >= 600 or was != ON["light"]:        # 기록: 10분마다 + 조명이 바뀔 때
            LOGGED_AT = time.time()
            log("climate", {"ts": iso(t), "temp_c": T, "rh_pct": RH, "light": int(ON["light"]), "fan": int(ON["fan"])})
        due = [x for x in S["times"] if x not in S["done"] and 0 <= minutes_past(t, x) < 60]
        if not S.get("watch_from"):                             # 비상 급수 시계: 물을 한 번도 안 줬으면 처음 켠 때부터 센다
            S["watch_from"] = iso(t)
            save()
        extra = extra_watering(t, T, RH, due)
        if due or extra:
            S["done"] += due
            if extra == "dry":
                S["extra_day"] = S["day"]
            save()
    for _ in due:                                               # 자동 급수 (1시간 넘게 늦었으면 건너뛴다)
        try:
            water(ml=S["each"], by="schedule")
        except Oops as e:
            log("water", {"ts": iso(), "ml": 0, "by": "schedule", "skipped": str(e)})
    if extra:
        by = "auto" if extra == "dry" else "fallback"
        try:
            dose = water(ml=S["each"], by=by)
            did("water", f"물: 덥고 건조해서 {dose['ml']:g} mL 를 한 번 더 줬어요 ({T:g} °C · {RH:g} %)" if extra == "dry"
                else f"물: {FALLBACK_H}시간 동안 급수가 없어 {dose['ml']:g} mL 를 줬어요 (비상 급수 — 서버의 급수 예측을 확인하세요)")
        except Oops as e:
            log("water", {"ts": iso(), "ml": 0, "by": by, "skipped": str(e)})


def extra_watering(t, T, RH, due):
    """시간표 밖의 급수 → 'fallback' (FALLBACK_H 동안 물을 안 줌 — 서버가 멈췄을 때) · 'dry' (덥고 건조, DRY_EXTRA 일 때만) · None.
    평소의 급수 시점은 서버의 급수 예측(hub)이 water 명령으로 정한다."""
    if not S["flow"] or S["stopped"] or due:
        return None
    ref = S.get("last_given") or S.get("watch_from")
    if ref and (t - datetime.fromisoformat(ref)).total_seconds() / 3600 >= FALLBACK_H:
        return "fallback"
    since = (t - datetime.fromisoformat(S["last"]["ts"])).total_seconds() / 3600 if S["last"] else 99
    if (DRY_EXTRA and T is not None and RH is not None and T >= DRY_T and RH <= DRY_RH
            and since >= 3 and S.get("extra_day") != S["day"]):
        return "dry"
    return None


def minutes_past(t, hhmm):
    h, m = map(int, hhmm.split(":"))
    return (t.hour - h) * 60 + t.minute - m


def open_gpio():
    """핀 헤더가 달린 GPIO 칩을 연다. Pi 4 까지는 0번, Pi 5 는 OS 에 따라 0번이나 4번이라 이름으로 찾는다."""
    import lgpio
    for n in (0, 4, 1, 2, 3):
        try:
            h = lgpio.gpiochip_open(n)
        except Exception:
            continue
        try:
            label = str(lgpio.gpio_get_chip_info(h)[-1])
        except Exception:
            label = ""
        if "pinctrl-bcm" in label or "pinctrl-rp1" in label:
            print(f"GPIO: gpiochip{n} ({label})", file=sys.stderr)
            return h
        lgpio.gpiochip_close(h)
    print("GPIO: 칩 이름을 읽지 못해 gpiochip0 을 씁니다", file=sys.stderr)
    return lgpio.gpiochip_open(0)


def loop():
    global READ_AT
    while True:
        try:
            if time.time() - READ_AT >= 60:                     # 온습도는 1분마다
                READ_AT = time.time()
                read_sensor()
            tick()
        except Exception as e:
            print("tick:", repr(e), file=sys.stderr)
        time.sleep(2)


# ───────────── 맥북이 부르는 것 (HTTP) ─────────────
def state():
    if PI != 1:
        return {"ok": True, "pi": PI, "time": iso()}
    return {"ok": True, "pi": PI, "time": iso(),
            "light": {"on": ON["light"], "mode": S["light"], "start": S["start"], "hours": S["hours"], "why": WHY["light"]},
            "fan": {"on": ON["fan"], "mode": S["fan"], "why": WHY["fan"]},
            "water": {"times": S["times"], "each_ml": S["each"], "flow_ml_s": S["flow"], "today_ml": S["today_ml"],
                      "max_dose_ml": MAX_DOSE_ML, "max_day_ml": MAX_DAY_ML, "stopped": S["stopped"],
                      "tank_ok": tank_ok(), "last": S["last"]},
            "climate": dict(C), "actions": tail("actions", 10),
            "turntable": {"deg": None if POS is None else round(POS / STEPS * 360, 1), "at_home": at_home()},   # at_home = 원점 센서가 지금 자석을 보고 있나
            "db": {"url": DB_URL or None, **(LINK.status if LINK else {}), "scans_sent": len(S.get("scans_sent", [])),
                   "scans_skipped": S.get("scans_bad", {})}}


def tail(name, n):
    """기록 파일의 마지막 n 줄."""
    f = DATA / f"{name}.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()[-n:]] if f.exists() else []


def power_off():
    """이 Pi 를 안전하게 끈다 (shutdown). 초록 불이 멈추면 전원을 빼도 된다."""
    model = Path("/proc/device-tree/model")
    if not (model.exists() and "Raspberry Pi" in model.read_text(errors="ignore")):
        raise Oops("라즈베리파이가 아니라서 끄지 않았어요")
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        raise Oops("비밀번호 없이 끌 권한이 없어요 — ssh 로 들어가 sudo shutdown -h now")
    threading.Timer(1.0, subprocess.run, args=(["sudo", "-n", "shutdown", "-h", "now"],)).start()
    return {"ok": True, "message": "1초 뒤에 꺼져요. 초록 불이 멈추면 전원을 빼세요"}


def hhmm(text):
    h, m = text.strip().split(":")
    if not (0 <= int(h) < 24 and 0 <= int(m) < 60):
        raise ValueError(text)
    return f"{int(h):02d}:{int(m):02d}"


def handle(path, q):
    global HOLD
    if path == "/photo":
        return photo()
    if path == "/state":
        return state()
    if path == "/off":                  # 안전하게 끄기 — 브라우저로 http://<Pi>:8765/off 또는 off.sh
        return power_off()
    if PI != 1:
        raise Oops("이 Pi 는 카메라만 맡아요 (PI=2)")
    if path in ("/light", "/fan"):
        name, mode = path[1:], q.get("mode", "")
        if mode not in ("", "on", "off", "auto"):
            raise Oops("on · off · auto 중 하나여야 해요")
        if mode:
            S[name] = mode
        if name == "light" and q.get("start"):
            S["start"] = hhmm(q["start"])
        if name == "light" and q.get("hours"):
            S["hours"] = min(24.0, max(0.0, float(q["hours"])))
        save()
        tick()
        return {"ok": True, name: state()[name]}
    if path == "/water":
        dose = None
        if "stop" in q:
            S["stopped"] = q["stop"] == "1"
        elif "flow" in q:
            S["flow"] = max(0.0, float(q["flow"]))
        elif "times" in q:
            times = [] if q["times"] in ("", "off") else sorted({hhmm(x) for x in q["times"].split(",")})
            each = float(q.get("each") or S["each"])
            if not 0 < each <= MAX_DOSE_ML or each * len(times) > MAX_DAY_ML:
                raise Oops(f"한 번에 {MAX_DOSE_ML} mL, 하루에 {MAX_DAY_ML} mL 까지만 돼요")
            S.update(times=times, each=each, done=[x for x in times if minutes_past(now(), x) >= 0])   # 지난 시각은 내일부터
        elif "sec" in q:
            dose = water(sec=float(q["sec"]), by="test")
        elif "ml" in q:
            dose = water(ml=float(q["ml"]))
        save()
        return {"ok": True, **({"dose": dose} if dose else {}), "water": state()["water"]}
    if path == "/climate":
        read_sensor()
        return {"ok": C["error"] is None, **C, "light": ON["light"], "fan": ON["fan"]}      # 못 읽었으면 ok 가 false 이고 error 에 까닭
    if path == "/log":                  # 기록 파일 통째로: /log?name=climate | water | actions
        f = DATA / f"{q.get('name') if q.get('name') in ('water', 'actions') else 'climate'}.jsonl"
        return f.read_bytes() if f.exists() else b""
    if path == "/db":                   # 지금 바로 서버로 올리기 (온습도 · 급수 · 조명 · 팬 · 스캔 · 상태)
        if not LINK:
            raise Oops("서버 주소가 없어요 — Pi ① 의 pi/pi.env 에 DB_URL 을 적고 sudo systemctl restart greentwin")
        try:
            return {"ok": True, "sent": LINK.sync(), "url": DB_URL}
        except link.Fail as e:
            LINK.status.update(ok=False, error=str(e))
            raise Oops(str(e))
    if path in ("/home", "/goto"):
        with LOCK["motor"]:
            moved = home() if path == "/home" else goto(float(q.get("deg", 0)))
        return {"ok": True, "deg": round(POS / STEPS * 360, 1), "moved_deg": round((moved or 0) / STEPS * 360, 1)}
    if path == "/hold":                 # 스캔하는 동안 조명 켜고 팬 끄기. sec=0 이면 풀기
        HOLD = time.time() + min(900.0, float(q.get("sec", 0)))
        tick()
        return {"ok": True, "light": ON["light"], "fan": ON["fan"]}
    raise Oops(f"모르는 명령이에요: {path}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        try:
            body = handle(u.path, dict(parse_qsl(u.query, keep_blank_values=True)))
        except Oops as e:
            body = {"ok": False, "error": str(e)}
        except ValueError as e:
            body = {"ok": False, "error": f"값을 알아볼 수 없어요 ({e})"}
        except Exception as e:
            body = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        kind = "application/json"
        if isinstance(body, bytes):
            kind = "image/jpeg" if u.path == "/photo" else "text/plain; charset=utf-8"
        else:
            body = (json.dumps(body, ensure_ascii=False) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        S.update(json.loads((DATA / "state.json").read_text()))
    except (OSError, ValueError):
        pass                            # 처음 켰거나 파일이 깨졌으면 기본값으로
    if PI == 1:
        import lgpio
        H = open_gpio()
        for pin in RELAYS.values():     # 잡는 순간부터 꺼짐. LOW 에 켜지는 5 V 모듈은 끌 때 핀을 띄운다(오픈 드레인)
            try:
                lgpio.gpio_claim_output(H, pin, 1 - RELAY_ON, 0 if RELAY_ON else lgpio.SET_OPEN_DRAIN | lgpio.SET_PULL_NONE)
            except lgpio.error:
                lgpio.gpio_claim_output(H, pin, 1 - RELAY_ON)
        lgpio.group_claim_output(H, COILS, [0, 0, 0, 0])
        lgpio.gpio_claim_input(H, PIN_HOME, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_input(H, PIN_TANK, lgpio.SET_PULL_UP)
        threading.Thread(target=loop, daemon=True).start()
        if DB_URL:                      # 팀 서버와 잇기 (pi/link.py): 온습도 MQTT · 기록 · 스캔 · 대시보드 명령
            LINK = link.Link(sys.modules[__name__])
            LINK.start()
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    print(f"GreenTwin Pi {PI} — http://0.0.0.0:{PORT}", file=sys.stderr)
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    finally:
        if PI == 1:
            WHY.update(light="프로그램이 꺼져서", fan="프로그램이 꺼져서")
            KIND.update(light="stop", fan="stop")
            for name in RELAYS:
                relay(name, False)
            lgpio.group_write(H, COILS[0], 0)
