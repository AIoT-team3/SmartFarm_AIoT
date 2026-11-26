"""허브 분석 — 제출된 스캔(status = processing)에 생장 예측을 붙이고 completed 로 바꾼다.

  cd server && .venv/bin/python -m hub.worker          30초마다 새 스캔을 본다 (켜 두기)
  cd server && .venv/bin/python -m hub.worker --once   쌓인 스캔만 처리하고 끝

스캔 하나마다 (오래된 것부터):
  1) DB → 임시 폴더: 그 스캔 때까지의 키(scan) · 온습도 · 조명(sensor_reading) · 급수(actuation) · 스캔 주의(event scan.notes)
  2) device/foresee/foresee.py 를 '그 스캔 때'(NOW)로 돌린다 — Pi 에서 도는 것과 같은 코드
  3) 결과 → prediction (그 스캔에서 1~28일 뒤 키: p10 · 가운데 · p90)
           backtest   (예전 예측 중 이 스캔 날을 맞히려던 것을 채점)
           growth_state (수확 예상 · 우리 화분의 빠르기 · 스스로 채점 · 의심 스캔 연속 수)
           event      (앞뒤와 너무 다른 스캔이면 growth.suspect_scan)
  4) scan.status = completed
설정 (server/.env, 없어도 된다): HUB_PLANT (greenonion | basil, 비우면 plant.species 로), HUB_TARGET_CM (수확할 키), FORESEE (foresee.py 경로) · 급수: WATER_THRESHOLD · WATER_ML · HUB_API_URL · HUB_MQTT_HOST · COMMAND_PASSWORD
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.database import get_connection

KOREA = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parents[2]
FORESEE = Path(os.getenv("FORESEE") or ROOT / "device" / "foresee" / "foresee.py")
NS = uuid.UUID("6f1c2a52-6d8e-4c1e-9a43-5b0a7c2e9d11")         # device/pi/link.py 와 같은 이름공간
HORIZON = 28
SPECIES = {"greenonion": "greenonion", "green onion": "greenonion", "대파": "greenonion", "파": "greenonion",
           "basil": "basil", "바질": "basil"}
RETRY_S = 600
WATER_EVERY = timedelta(hours=1)        # 급수 예측을 새로 하는 간격 (docs/watering-forecast-design.md 5장)
WATER_COOLDOWN = timedelta(hours=1)     # 명령을 넣은 뒤 이만큼은 다시 넣지 않는다 (실패했어도)
WATER_MIN_GAP = timedelta(hours=3)      # 마지막 급수 뒤 이만큼은 주지 않는다
CHECK_RATIO = 0.9                       # 줄 시각에 실제 누적이 기준치의 이만큼은 돼야 준다
API_URL = os.getenv("HUB_API_URL", "http://127.0.0.1:8000")
MQTT_HOST, MQTT_PORT = os.getenv("HUB_MQTT_HOST", "127.0.0.1"), int(os.getenv("HUB_MQTT_PORT", "1884"))
WATER_TOPIC = "smartfarm/watering/forecast"


def kst(ts: datetime) -> str:
    return ts.astimezone(KOREA).isoformat(timespec="seconds")


def plant_kind(species: str) -> str:
    return os.getenv("HUB_PLANT") or SPECIES.get((species or "").strip().lower(), "greenonion")


def model_files() -> tuple[Path | None, str]:
    """foresee 코드(+ 사전학습 model.json)의 지문 — 바뀌면 model_version 을 새로 만든다."""
    model = FORESEE.with_name("model.json")
    digest = hashlib.sha256(FORESEE.read_bytes())
    for extra in (FORESEE.with_name("watering.py"), model, FORESEE.with_name("watering_model.json")):
        if extra.exists():
            digest.update(extra.read_bytes())
    digest.update(f"{os.getenv('WATER_THRESHOLD', '1000')}/{os.getenv('WATER_ML', '25')}".encode())   # 급수 기준치 · 양이 바뀌어도 새 버전
    return (model if model.exists() else None), digest.hexdigest()[:12]


def growth_model(cur, plant: str, trained: bool) -> int:
    model, code = model_files()
    cur.execute(
        """SELECT id FROM model_version WHERE kind = 'growth' AND params->>'name' = 'foresee'
           AND params->>'code' = %s AND params->>'plant' = %s ORDER BY id DESC LIMIT 1""",
        (code, plant),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    params = {"name": "foresee", "code": code, "plant": plant, "trained": trained, "file": "device/foresee/foresee.py",
              "text": "생장식 × 우리 화분 스캔에서 배우는 빠르기 · 꼴 · 다 자란 키 (칼만 필터)" + (" × 사전학습 신경망" if trained else "")}
    cur.execute(
        """INSERT INTO model_version (kind, status, params, trained_at, file_path)
           VALUES ('growth', 'active', %s, now(), %s) RETURNING id""",
        (Jsonb(params), str(model.relative_to(ROOT)) if model else "device/foresee/foresee.py"),
    )
    new = cur.fetchone()["id"]
    cur.execute(  # 예전 foresee 버전은 보관 — 대시보드는 가장 최근의 active 를 본다
        """UPDATE model_version SET status = 'archived'
           WHERE kind = 'growth' AND params->>'name' = 'foresee' AND status = 'active' AND id <> %s""",
        (new,),
    )
    return new


def write_inputs(cur, data: Path, plant_id: int, until: datetime, sown: datetime) -> dict[str, list]:
    """DB → foresee 가 읽는 파일 (Pi 의 data 폴더와 같은 꼴). → 스캔마다의 주의 문장"""
    cur.execute(
        """SELECT data->>'scan_id' AS scan_id, data->'notes' AS notes FROM event
           WHERE plant_id = %s AND kind = 'scan.notes'""",
        (plant_id,),
    )
    notes = {r["scan_id"]: r["notes"] for r in cur.fetchall() if isinstance(r["notes"], list)}
    cur.execute(
        """SELECT id, ts, height_cm FROM scan
           WHERE plant_id = %s AND status IN ('processing', 'completed') AND height_cm IS NOT NULL AND ts <= %s
           ORDER BY ts, id""",
        (plant_id, until),
    )
    rows = [{"scan_id": str(r["id"]), "ts": kst(r["ts"]), "day": round((r["ts"] - sown).total_seconds() / 86400, 3),
             "height_cm": r["height_cm"], "notes": notes.get(str(r["id"]), [])} for r in cur.fetchall()]
    (data / "scans").mkdir(parents=True)
    (data / "scans" / "index.json").write_text(json.dumps(rows, ensure_ascii=False))

    cur.execute(
        """SELECT ts, air_temp_c, rh_pct, light_level FROM sensor_reading
           WHERE plant_id = %s AND ts <= %s AND ts > %s - interval '90 days' ORDER BY ts""",
        (plant_id, until, until),
    )
    with open(data / "climate.jsonl", "w", encoding="utf-8") as f:
        for r in cur:
            f.write(json.dumps({"ts": kst(r["ts"]), "temp_c": r["air_temp_c"], "rh_pct": r["rh_pct"],
                                "light": r["light_level"]}) + "\n")
    cur.execute(
        """SELECT ts, amount, source, context->>'by' AS by FROM actuation
           WHERE plant_id = %s AND kind = 'water' AND ok AND ts <= %s ORDER BY ts""",
        (plant_id, until),
    )
    with open(data / "water.jsonl", "w", encoding="utf-8") as f:
        for r in cur:
            f.write(json.dumps({"ts": kst(r["ts"]), "ml": r["amount"] or 0, "by": r["by"] or r["source"]}) + "\n")
    return notes


def run_foresee(data: Path, now: datetime, sown: datetime, plant: str) -> dict:
    env = {**os.environ, "DATA": str(data), "SOWN": kst(sown), "PLANT": plant, "NOW": kst(now),
           "TZ": "Asia/Seoul", "PYTHONUTF8": "1", "MODEL": str(FORESEE.with_name("model.json"))}
    if os.getenv("HUB_TARGET_CM"):
        env["TARGET_CM"] = os.environ["HUB_TARGET_CM"]
    r = subprocess.run([sys.executable, str(FORESEE)], env=env, capture_output=True, text=True, timeout=600)
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {"ok": False, "error": (r.stderr or "출력이 없어요").strip()[-500:]}
    if not out.get("ok"):
        raise RuntimeError(out.get("error"))
    return json.loads((data / "foresee" / "forecast.json").read_text())


def process(conn, scan: dict) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT sown_at, species FROM plant WHERE id = %s", (scan["plant_id"],))
        plant_row = cur.fetchone()
        sown = plant_row["sown_at"]
        if scan["height_cm"] is None:                       # 키를 못 잰 스캔 (빈 화분 · 식물이 안 보임): 예측 없이 끝
            cur.execute("UPDATE scan SET status = 'completed' WHERE id = %s", (scan["id"],))
            return {"scan": str(scan["id"]), "predictions": 0, "note": "키 없음"}
        plant = plant_kind(plant_row["species"])
        with tempfile.TemporaryDirectory(prefix="hub-") as tmp:
            notes = write_inputs(cur, Path(tmp), scan["plant_id"], scan["ts"], sown)
            doc = run_foresee(Path(tmp), scan["ts"], sown, plant)
        model_id = growth_model(cur, plant, bool(doc["model"]["trained"]))
        sown_day = sown.astimezone(KOREA).date()

        made = 0
        for c in doc["curve"]:
            target = (date.fromisoformat(c["date"]) - sown_day).days
            if scan["day"] < target <= scan["day"] + HORIZON:
                cur.execute(
                    """INSERT INTO prediction (model_id, origin_scan_id, origin_day, target_day, height_mu, p10, p90)
                       VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (model_id, scan["id"], scan["day"], target, max(c["p50"], 0.0), max(c["p10"], 0.0), max(c["p90"], 0.0)),
                )
                made += cur.rowcount

        last = doc["observed"][-1] if doc["observed"] else {}
        suspect = bool(last.get("suspect")) and last.get("ts") == kst(scan["ts"])
        out_of_frame = any("사진 밖" in n for n in notes.get(str(scan["id"]), []))
        graded = 0
        if not suspect and not out_of_frame:                # 예전 예측 채점: 그 예측이 맞히려던 날의 첫 스캔으로
            cur.execute(
                """SELECT p.id, p.origin_day, p.target_day, p.height_mu, p.p10, p.p90
                   FROM prediction AS p JOIN scan AS s ON s.id = p.origin_scan_id
                   LEFT JOIN backtest AS b ON b.prediction_id = p.id
                   WHERE s.plant_id = %s AND s.ts < %s AND p.target_day = %s AND p.origin_day < %s AND b.id IS NULL""",
                (scan["plant_id"], scan["ts"], scan["day"], scan["day"]),
            )
            for p in cur.fetchall():
                obs = scan["height_cm"]
                cur.execute(
                    """INSERT INTO backtest (prediction_id, variant, origin_day, target_day, horizon, predicted, observed, abs_err, in_band)
                       VALUES (%s, 'foresee', %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (p["id"], p["origin_day"], p["target_day"], p["target_day"] - p["origin_day"], p["height_mu"], obs,
                     abs(obs - p["height_mu"]), None if p["p10"] is None else p["p10"] <= obs <= p["p90"]),
                )
                graded += cur.rowcount

        streak = 0
        for o in reversed(doc["observed"]):
            if not o.get("suspect"):
                break
            streak += 1
        posterior = {"source": "foresee", "origin_scan_id": str(scan["id"]), "made_at": doc["made_at"], "plant": plant,
                     "harvest": doc["harvest"], "personal": doc["personal"], "skill": doc["skill"], "env": doc["env"],
                     "what_if": doc["what_if"], "notes": doc["notes"], "scans": len(doc["observed"])}
        cur.execute(
            """INSERT INTO growth_state (plant_id, model_id, posterior, anomaly_streak, updated_at)
               VALUES (%s, %s, %s, %s, now())
               ON CONFLICT (plant_id) DO UPDATE SET model_id = EXCLUDED.model_id,
                   posterior = EXCLUDED.posterior || jsonb_strip_nulls(jsonb_build_object('watering', growth_state.posterior->'watering')),
                   anomaly_streak = EXCLUDED.anomaly_streak, updated_at = EXCLUDED.updated_at""",
            (scan["plant_id"], model_id, Jsonb(posterior), streak),
        )
        if suspect:
            cur.execute(
                """INSERT INTO event (id, plant_id, ts, kind, level, message, data)
                   VALUES (%s, %s, %s, 'growth.suspect_scan', 'warning', %s, %s) ON CONFLICT (id) DO NOTHING""",
                (uuid.uuid5(NS, f"{scan['plant_id']}/suspect/{scan['id']}"), scan["plant_id"], scan["ts"],
                 f"{scan['day']}일째 스캔({scan['height_cm']:g} cm)이 앞뒤와 너무 달라 예측에서 덜 믿었어요 — 잘못 찍혔는지 사진을 확인하세요",
                 Jsonb({"scan_id": str(scan["id"]), "height_cm": scan["height_cm"]})),
            )
        cur.execute("UPDATE scan SET status = 'completed' WHERE id = %s", (scan["id"],))
        return {"scan": str(scan["id"]), "plant_id": scan["plant_id"], "day": scan["day"], "model_id": model_id, "predictions": made, "graded": graded,
                "suspect": suspect, "harvest": doc["harvest"]["date"].get("p50")}


def run_once(skip: dict) -> list[dict]:
    done = []
    while True:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """SELECT id, plant_id, ts, day, height_cm FROM scan
                       WHERE status = 'processing' AND NOT (id = ANY(%s))
                       ORDER BY ts, id LIMIT 1 FOR UPDATE SKIP LOCKED""",
                    ([k for k, t in skip.items() if time.time() < t],),
                )
                scan = cur.fetchone()
            if scan is None:
                return done
            try:
                result = process(conn, scan)
            except Exception as e:                           # 예측이 실패해도 스캔은 processing 으로 두고 10분 뒤 다시
                conn.rollback()
                skip[scan["id"]] = time.time() + RETRY_S
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO event (id, plant_id, ts, kind, level, message, data)
                           VALUES (%s, %s, now(), 'growth.error', 'error', %s, %s) ON CONFLICT (id) DO NOTHING""",
                        (uuid.uuid4(), scan["plant_id"], f"생장 예측 실패: {e}"[:2000], Jsonb({"scan_id": str(scan["id"])})),
                    )
                conn.commit()
                print(json.dumps({"scan": str(scan["id"]), "error": str(e)}, ensure_ascii=False), flush=True)
                continue
            conn.commit()
            done.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def add_event(cur, plant_id: int, key: str, kind: str, level: str, message: str, data: dict, ts: datetime):
    """같은 key 의 이벤트는 한 번만 남는다."""
    cur.execute(
        """INSERT INTO event (id, plant_id, ts, kind, level, message, data)
           VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
        (uuid.uuid5(NS, f"{plant_id}/{key}"), plant_id, ts, kind, level, message, Jsonb(data)),
    )


def refresh_watering(cur, plant_id: int, now: datetime) -> dict:
    """지금 시각으로 foresee 를 돌려 growth_state.posterior.watering 만 바꾼다 (생장 예측은 그대로) → watering."""
    cur.execute("SELECT sown_at, species FROM plant WHERE id = %s", (plant_id,))
    row = cur.fetchone()
    plant = plant_kind(row["species"])
    with tempfile.TemporaryDirectory(prefix="hub-") as tmp:
        write_inputs(cur, Path(tmp), plant_id, now, row["sown_at"])
        doc = run_foresee(Path(tmp), now, row["sown_at"], plant)
    watering = doc.get("watering") or {"method": "none", "notes": ["foresee 가 급수 예측을 내지 않았어요"]}
    cur.execute("SELECT model_id, posterior->'watering'->>'method' AS method FROM growth_state WHERE plant_id = %s", (plant_id,))
    before = cur.fetchone()
    # 생장 모델 버전은 스캔 처리만 바꾼다 — 여기서 새 버전을 만들면 대시보드의 생장 예측이 비어 보인다
    model_id = before["model_id"] if before else growth_model(cur, plant, bool(doc["model"]["trained"]))
    cur.execute(
        """INSERT INTO growth_state (plant_id, model_id, posterior, updated_at)
           VALUES (%s, %s, %s, now())
           ON CONFLICT (plant_id) DO UPDATE SET
               posterior = jsonb_set(growth_state.posterior, '{watering}', EXCLUDED.posterior->'watering'),
               updated_at = EXCLUDED.updated_at""",
        (plant_id, model_id, Jsonb({"source": "foresee", "watering": watering})),
    )
    if watering["method"] == "none" and (before or {}).get("method") != "none":     # 끊기기 시작할 때 한 번만
        add_event(cur, plant_id, f"watering/no_data/{now.astimezone(KOREA):%Y%m%d%H}", "watering.no_data", "warning",
                  " · ".join(watering.get("notes") or ["급수 시점을 예측하지 못했어요"]), {"made_at": watering.get("made_at")}, now)
    return watering


def post_command(plant_id: int, request_id: uuid.UUID, ml: float) -> dict:
    """서버의 명령 API 로 water 명령을 넣는다 (대시보드와 같은 길: 검증 · 중복 방지 · 만료가 그대로)."""
    body = json.dumps({"request_id": str(request_id), "plant_id": plant_id, "kind": "water",
                       "payload": {"amount_ml": ml}}).encode()
    req = urllib.request.Request(f"{API_URL}/api/v1/commands", body, method="POST", headers={
        "Content-Type": "application/json", "X-Command-Password": os.getenv("COMMAND_PASSWORD", "")})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def publish(plant_id: int, watering: dict):
    """MQTT smartfarm/watering/forecast (retain) — 브로커가 없어도 hub 는 멈추지 않는다."""
    msg = {"plant_id": plant_id, **{k: watering.get(k) for k in ("made_at", "hours_left", "need_at", "plan", "method")}}
    try:
        from paho.mqtt import publish as mqtt
        mqtt.single(WATER_TOPIC, json.dumps(msg, ensure_ascii=False), qos=1, retain=True, hostname=MQTT_HOST, port=MQTT_PORT)
    except Exception as e:
        print(f"hub: MQTT 로 급수 예측을 보내지 못했어요 ({e})", file=sys.stderr, flush=True)


def last_planned(cur, plant_id: int) -> dict | None:
    """hub 가 마지막으로 명령을 넣으려 한 때 (넣었거나, 넣다가 실패했거나) — 그 뒤 WATER_COOLDOWN 동안은 다시 넣지 않는다."""
    cur.execute(
        """SELECT ts, data FROM event
           WHERE plant_id = %s AND kind IN ('watering.planned', 'watering.failed') AND data ? 'request_id'
           ORDER BY ts DESC LIMIT 1""",
        (plant_id,),
    )
    return cur.fetchone()


def failed_recently(cur, plant_id: int, now: datetime) -> bool:
    """급수 예측이 RETRY_S 안에 실패했으면 그동안은 다시 돌리지 않는다 (hub 를 다시 켜도)."""
    cur.execute(
        """SELECT 1 FROM event WHERE plant_id = %s AND kind = 'growth.error' AND data->>'part' = 'watering'
           AND ts > %s AND ts <= %s LIMIT 1""",
        (plant_id, now - timedelta(seconds=RETRY_S), now),
    )
    return cur.fetchone() is not None


def check_failed(cur, plant_id: int, now: datetime):
    """hub 가 넣은 명령이 실패했으면 watering.failed 를 한 번 남긴다 (다시 넣지는 않는다)."""
    cur.execute(
        """SELECT c.id, c.result FROM event AS e JOIN command AS c ON c.id = (e.data->>'command_id')::bigint
           WHERE e.plant_id = %s AND e.kind = 'watering.planned' AND c.status = 'failed'""",
        (plant_id,),
    )
    for c in cur.fetchall():
        code = (c["result"] or {}).get("code") or "FAILED"
        add_event(cur, plant_id, f"watering/failed/{c['id']}", "watering.failed", "error",
                  f"급수 명령이 실패했어요 ({code}) — 다음 예측 때 다시 판단해요", {"command_id": c["id"], "code": code}, now)


def decide(cur, plant_id: int, watering: dict, now: datetime) -> str:
    """줄 시각이 지났다: 실제 누적을 확인하고 water 명령을 넣거나 건너뛴다 → 'planned' | 'skipped'."""
    accum, threshold, last = watering.get("accum"), watering.get("threshold") or 0, watering.get("last_water")
    why = None
    if accum is None:
        why = "온습도 기록이 없어 확인하지 못했어요"
    elif last and now - parse(last["ts"]) < WATER_MIN_GAP:
        why = f"마지막 급수 뒤 {WATER_MIN_GAP.total_seconds() / 3600:g}시간이 안 지났어요"
    elif accum < CHECK_RATIO * threshold:
        why = f"아직 덜 말랐어요 (누적 {accum:g} / 기준 {threshold:g} kPa·min)"
    hour = f"{now.astimezone(KOREA):%Y%m%d%H}"
    if why:
        add_event(cur, plant_id, f"watering/skipped/{hour}", "watering.skipped", "info", f"급수 건너뜀: {why}",
                  {"accum": accum, "threshold": threshold, "plan": watering.get("plan")}, now)
        return "skipped"
    request_id = uuid.uuid5(NS, f"{plant_id}/watering/{hour}")
    ml = (watering.get("plan") or {}).get("ml") or float(os.getenv("WATER_ML", "25"))
    try:
        command = post_command(plant_id, request_id, ml)
    except Exception as e:
        add_event(cur, plant_id, f"watering/post_failed/{hour}", "watering.failed", "error", f"급수 명령을 넣지 못했어요: {e}"[:2000],
                  {"request_id": str(request_id)}, now)
        return "skipped"
    add_event(cur, plant_id, f"watering/planned/{hour}", "watering.planned", "info",
              f"VPD 누적 {accum:g} / {threshold:g} kPa·min — 물 {ml:g} mL 명령을 넣었어요",
              {"command_id": command["id"], "request_id": str(request_id), "accum": accum, "threshold": threshold,
               "plan": watering.get("plan")}, now)
    return "planned"


def watering_tick(last_run: dict, now: datetime | None = None) -> list[dict]:
    """화분마다: 1시간이 지났거나 줄 시각이 지났으면 급수 예측을 새로 하고, 줄 시각이 지났으면 확인 뒤 명령."""
    now = now or datetime.now(timezone.utc)
    done = []
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id FROM plant ORDER BY id")
            plants = [r["id"] for r in cur.fetchall()]
        for plant_id in plants:
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    check_failed(cur, plant_id, now)
                    cur.execute("SELECT posterior->'watering' AS w FROM growth_state WHERE plant_id = %s", (plant_id,))
                    row = cur.fetchone()
                    plan = ((row or {}).get("w") or {}).get("plan")
                    recent = last_planned(cur, plant_id)
                    due = bool(plan) and parse(plan["water_at"]) <= now and not (recent and now - recent["ts"] < WATER_COOLDOWN)
                    stale = plant_id not in last_run or now - last_run[plant_id] >= WATER_EVERY
                    if not (due or stale) or failed_recently(cur, plant_id, now):
                        conn.commit()
                        continue
                    watering = refresh_watering(cur, plant_id, now)
                    last_run[plant_id] = now
                    result = decide(cur, plant_id, watering, now) if due else "refreshed"
                conn.commit()
                publish(plant_id, watering)
                done.append({"plant_id": plant_id, "watering": result, "hours_left": watering.get("hours_left"),
                             "method": watering.get("method")})
            except Exception as e:                           # 예측이 실패해도 다른 화분 · 다음 반복은 계속
                conn.rollback()
                last_run[plant_id] = now - WATER_EVERY + timedelta(seconds=RETRY_S)     # 10분 뒤 다시
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO event (id, plant_id, ts, kind, level, message, data)
                           VALUES (%s, %s, %s, 'growth.error', 'error', %s, %s) ON CONFLICT (id) DO NOTHING""",
                        (uuid.uuid4(), plant_id, now, f"급수 예측 실패: {e}"[:2000], Jsonb({"part": "watering"})),
                    )
                conn.commit()
                print(json.dumps({"plant_id": plant_id, "watering_error": str(e)}, ensure_ascii=False), flush=True)
    for d in done:
        print(json.dumps(d, ensure_ascii=False), flush=True)
    return done


def main():
    if not FORESEE.exists():
        sys.exit(f"foresee.py 가 없어요: {FORESEE} (저장소의 device/ 폴더가 필요해요)")
    skip, last_run = {}, {}
    if "--once" in sys.argv:
        run_once(skip)
        watering_tick(last_run)
        return
    print(f"허브 분석 대기 중 — {FORESEE}", file=sys.stderr, flush=True)
    while True:
        try:
            for done in run_once(skip):
                last_run.pop(done.get("plant_id"), None)     # 새 스캔이 들어왔으면 급수 예측도 바로 새로
            watering_tick(last_run)
        except Exception as e:                               # DB 가 잠깐 안 보여도 멈추지 않는다
            print(f"hub: {datetime.now(timezone.utc).isoformat()} {e}", file=sys.stderr, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
