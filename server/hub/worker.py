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
설정 (server/.env, 없어도 된다): HUB_PLANT (greenonion | basil, 비우면 plant.species 로), HUB_TARGET_CM (수확할 키), FORESEE (foresee.py 경로)
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timezone
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


def kst(ts: datetime) -> str:
    return ts.astimezone(KOREA).isoformat(timespec="seconds")


def plant_kind(species: str) -> str:
    return os.getenv("HUB_PLANT") or SPECIES.get((species or "").strip().lower(), "greenonion")


def model_files() -> tuple[Path | None, str]:
    """foresee 코드(+ 사전학습 model.json)의 지문 — 바뀌면 model_version 을 새로 만든다."""
    model = FORESEE.with_name("model.json")
    digest = hashlib.sha256(FORESEE.read_bytes())
    if model.exists():
        digest.update(model.read_bytes())
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


def write_inputs(cur, data: Path, scan: dict, sown: datetime) -> dict[str, list]:
    """DB → foresee 가 읽는 파일 (Pi 의 data 폴더와 같은 꼴). → 스캔마다의 주의 문장"""
    cur.execute(
        """SELECT data->>'scan_id' AS scan_id, data->'notes' AS notes FROM event
           WHERE plant_id = %s AND kind = 'scan.notes'""",
        (scan["plant_id"],),
    )
    notes = {r["scan_id"]: r["notes"] for r in cur.fetchall() if isinstance(r["notes"], list)}
    cur.execute(
        """SELECT id, ts, height_cm FROM scan
           WHERE plant_id = %s AND status IN ('processing', 'completed') AND height_cm IS NOT NULL AND ts <= %s
           ORDER BY ts, id""",
        (scan["plant_id"], scan["ts"]),
    )
    rows = [{"scan_id": str(r["id"]), "ts": kst(r["ts"]), "day": round((r["ts"] - sown).total_seconds() / 86400, 3),
             "height_cm": r["height_cm"], "notes": notes.get(str(r["id"]), [])} for r in cur.fetchall()]
    (data / "scans").mkdir(parents=True)
    (data / "scans" / "index.json").write_text(json.dumps(rows, ensure_ascii=False))

    cur.execute(
        """SELECT ts, air_temp_c, rh_pct, light_level FROM sensor_reading
           WHERE plant_id = %s AND ts <= %s AND ts > %s - interval '90 days' ORDER BY ts""",
        (scan["plant_id"], scan["ts"], scan["ts"]),
    )
    with open(data / "climate.jsonl", "w", encoding="utf-8") as f:
        for r in cur:
            f.write(json.dumps({"ts": kst(r["ts"]), "temp_c": r["air_temp_c"], "rh_pct": r["rh_pct"],
                                "light": r["light_level"]}) + "\n")
    cur.execute(
        """SELECT ts, amount, source, context->>'by' AS by FROM actuation
           WHERE plant_id = %s AND kind = 'water' AND ok AND ts <= %s ORDER BY ts""",
        (scan["plant_id"], scan["ts"]),
    )
    with open(data / "water.jsonl", "w", encoding="utf-8") as f:
        for r in cur:
            f.write(json.dumps({"ts": kst(r["ts"]), "ml": r["amount"] or 0, "by": r["by"] or r["source"]}) + "\n")
    return notes


def run_foresee(data: Path, scan: dict, sown: datetime, plant: str) -> dict:
    env = {**os.environ, "DATA": str(data), "SOWN": kst(sown), "PLANT": plant, "NOW": kst(scan["ts"]),
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
            notes = write_inputs(cur, Path(tmp), scan, sown)
            doc = run_foresee(Path(tmp), scan, sown, plant)
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
               ON CONFLICT (plant_id) DO UPDATE SET model_id = EXCLUDED.model_id, posterior = EXCLUDED.posterior,
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
        return {"scan": str(scan["id"]), "day": scan["day"], "model_id": model_id, "predictions": made, "graded": graded,
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


def main():
    if not FORESEE.exists():
        sys.exit(f"foresee.py 가 없어요: {FORESEE} (저장소의 device/ 폴더가 필요해요)")
    skip = {}
    if "--once" in sys.argv:
        run_once(skip)
        return
    print(f"허브 분석 대기 중 — {FORESEE}", file=sys.stderr, flush=True)
    while True:
        try:
            run_once(skip)
        except Exception as e:                               # DB 가 잠깐 안 보여도 멈추지 않는다
            print(f"hub: {datetime.now(timezone.utc).isoformat()} {e}", file=sys.stderr, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
