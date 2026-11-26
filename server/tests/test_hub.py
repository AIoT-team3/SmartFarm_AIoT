import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from hub import worker
from tests.postgres_case import PostgresIntegrationCase


KST = timezone(timedelta(hours=9))


@unittest.skipUnless(os.getenv("RUN_HUB_DB_TESTS") == "1", "RUN_HUB_DB_TESTS=1로 실행하세요.")
class HubIntegrationTests(PostgresIntegrationCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("hub.worker.get_connection", self.connect))
        self.enterContext(patch("app.services.growth.get_connection", self.connect))
        self.client = self.enterContext(TestClient(app))
        self.sown = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=9)
        with self.connect() as conn:
            conn.execute(
                "UPDATE plant SET sown_at = %s, species = 'greenonion' WHERE id = %s", (self.sown, self.plant_id)
            )

    def scan(self, conn, day, hour, height, status="processing"):
        scan_id = uuid4()
        ts = self.sown + timedelta(days=day, hours=hour)
        conn.execute(
            "INSERT INTO scan (id, plant_id, ts, day, height_cm, status) VALUES (%s, %s, %s, %s, %s, %s)",
            (scan_id, self.plant_id, ts, day, height, status),
        )
        return scan_id

    def test_processing_scans_get_predictions_backtests_and_harvest(self):
        with self.connect() as conn:
            scans = [self.scan(conn, day, hour, round(11 + 0.85 * (day + hour / 24 - 2), 1))
                     for day in range(2, 9) for hour in (8, 18)]
            odd = self.scan(conn, 9, 8, 2.0)                      # 앞뒤와 너무 다른 스캔
            empty = self.scan(conn, 9, 13, None)                  # 키를 못 잰 스캔
            self.scan(conn, 9, 14, 30.0, status="pending")        # 아직 제출 전 — 건드리지 않는다

        done = worker.run_once({})
        self.assertEqual(len(done), len(scans) + 2)

        with self.connect() as conn:
            status = dict(conn.execute("SELECT status, count(*) FROM scan GROUP BY status").fetchall())
            self.assertEqual(status, {"completed": len(scans) + 2, "pending": 1})
            per_scan = dict(conn.execute(
                "SELECT origin_scan_id, count(*) FROM prediction GROUP BY origin_scan_id"
            ).fetchall())
            self.assertEqual(per_scan.get(scans[-1]), worker.HORIZON)
            self.assertNotIn(empty, per_scan)
            graded, err = conn.execute("SELECT count(*), avg(abs_err) FROM backtest").fetchone()
            self.assertGreater(graded, 10)
            self.assertLess(err, 1.5)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM backtest b JOIN prediction p ON p.id = b.prediction_id WHERE b.observed = 2.0"
            ).fetchone()[0], 0)
            events = conn.execute("SELECT kind, data->>'scan_id' FROM event").fetchall()
            self.assertIn(("growth.suspect_scan", str(odd)), events)
            state = conn.execute("SELECT anomaly_streak, posterior FROM growth_state").fetchone()
            self.assertEqual(state[0], 1)
            self.assertEqual(state[1]["source"], "foresee")

        body = self.client.get(f"/api/v1/plants/{self.plant_id}/growth").json()
        self.assertEqual(body["status"], "available")
        self.assertEqual(body["origin_scan"]["id"], str(odd))
        self.assertEqual(body["harvest"]["status"], "available")
        self.assertGreaterEqual(body["harvest"]["remaining_days"], 0)
        self.assertNotIn("posterior", body["growth_state"])

        self.assertEqual(worker.run_once({}), [])                  # 다시 돌려도 할 일이 없다



@unittest.skipUnless(os.getenv("RUN_HUB_DB_TESTS") == "1", "RUN_HUB_DB_TESTS=1로 실행하세요.")
class HubWateringTests(PostgresIntegrationCase):
    """급수 시점 예측 — docs/watering-forecast-design.md 5장."""

    def setUp(self):
        super().setUp()
        self.enterContext(patch("hub.worker.get_connection", self.connect))
        self.enterContext(patch("app.services.commands.get_connection", self.connect))
        self.enterContext(patch("app.services.growth.get_connection", self.connect))
        self.enterContext(patch.dict(os.environ, {"COMMAND_PASSWORD": "test-command-password",
                                                  "WATER_THRESHOLD": "300", "WATER_ML": "25"}))
        self.client = self.enterContext(TestClient(app))
        self.published = []
        self.enterContext(patch("hub.worker.publish", lambda plant_id, w: self.published.append((plant_id, w))))
        self.enterContext(patch("hub.worker.post_command", self.post_command))
        self.now = datetime.now(KST).replace(second=0, microsecond=0)
        with self.connect() as conn:
            conn.execute("UPDATE plant SET sown_at = %s, species = 'greenonion' WHERE id = %s",
                         (self.now - timedelta(days=10), self.plant_id))

    def post_command(self, plant_id, request_id, ml):
        r = self.client.post("/api/v1/commands", headers={"X-Command-Password": "test-command-password"},
                             json={"request_id": str(request_id), "plant_id": plant_id, "kind": "water", "payload": {"amount_ml": ml}})
        self.assertIn(r.status_code, (200, 201), r.text)
        return r.json()

    def readings(self, hours, temp=26.0, rh=40.0):
        """지금까지 hours 시간 동안 10분마다 온습도 (26 °C · 40 % ≈ VPD 2.0 kPa → 300 kPa·min 은 약 2.5시간)."""
        with self.connect() as conn:
            for k in range(hours * 6 + 1):
                ts = self.now - timedelta(minutes=10 * k)
                conn.execute(
                    """INSERT INTO sensor_reading (plant_id, ts, air_temp_c, rh_pct, light_level)
                       VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (self.plant_id, ts, temp, rh, 1),
                )

    def watered(self, hours_ago):
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO actuation (id, plant_id, ts, kind, amount, unit, ok, source, context)
                   VALUES (%s, %s, %s, 'water', 25, 'mL', true, 'auto', %s)""",
                (uuid4(), self.plant_id, self.now - timedelta(hours=hours_ago), json.dumps({"by": "auto"})),
            )

    def state(self):
        with self.connect() as conn:
            return conn.execute("SELECT posterior FROM growth_state WHERE plant_id = %s", (self.plant_id,)).fetchone()[0]

    def count(self, sql, *args):
        with self.connect() as conn:
            return conn.execute(sql, args).fetchone()[0]

    def test_refresh_writes_watering_and_publishes(self):
        self.readings(48)
        self.watered(1)
        done = worker.watering_tick({}, self.now)
        self.assertEqual([d["watering"] for d in done if d["plant_id"] == self.plant_id], ["refreshed"])
        w = self.state()["watering"]
        self.assertEqual(w["method"], "baseline")
        self.assertEqual(w["threshold"], 300)
        self.assertGreater(w["hours_left"], 0)
        self.assertGreaterEqual(datetime.fromisoformat(w["plan"]["water_at"]), self.now - timedelta(hours=1) + worker.WATER_MIN_GAP)
        self.assertEqual(self.published[-1][0], self.plant_id)
        self.assertEqual(self.count("SELECT count(*) FROM command"), 0)
        self.assertEqual(worker.watering_tick({self.plant_id: self.now}, self.now + timedelta(minutes=30)), [])   # 1시간 안 · 줄 시각 전이면 할 일 없음
        body = self.client.get(f"/api/v1/plants/{self.plant_id}/growth").json()
        self.assertEqual(body["watering"]["plan"], w["plan"])                     # 대시보드가 /growth 로 본다

    def test_refresh_keeps_growth_prediction(self):
        self.readings(48)
        with self.connect() as conn:
            for day in range(3, 9):
                conn.execute("INSERT INTO scan (id, plant_id, ts, day, height_cm, status) VALUES (%s, %s, %s, %s, %s, 'processing')",
                             (uuid4(), self.plant_id, self.now - timedelta(days=10 - day), day, 10 + day))
        worker.run_once({})
        before = self.state()
        predictions = self.count("SELECT count(*) FROM prediction")
        worker.watering_tick({}, self.now)
        after = self.state()
        self.assertEqual(after["harvest"], before["harvest"])
        self.assertEqual(self.count("SELECT count(*) FROM prediction"), predictions)
        self.assertEqual(after["watering"]["method"], "baseline")
        self.assertNotEqual(after["watering"]["size_factor"], 1.0)                  # 키가 있으니 크기 보정
        worker.run_once({})                                                          # 다음 스캔 처리도 watering 을 지우지 않는다
        with self.connect() as conn:
            conn.execute("INSERT INTO scan (id, plant_id, ts, day, height_cm, status) VALUES (%s, %s, %s, 9, 19.5, 'processing')",
                         (uuid4(), self.plant_id, self.now - timedelta(hours=1)))
        worker.run_once({})
        self.assertIn("watering", self.state())

    def test_due_plan_sends_one_command(self):
        self.readings(12)
        self.watered(6)                                                              # 6시간 동안 ≈ 700 kPa·min > 300
        worker.watering_tick({}, self.now)                                           # 첫 예측: 지금 필요 → 줄 시각 = 지금
        self.assertLessEqual(datetime.fromisoformat(self.state()["watering"]["plan"]["water_at"]), self.now)
        done = worker.watering_tick({self.plant_id: self.now}, self.now + timedelta(seconds=30))
        self.assertEqual(done[0]["watering"], "planned")
        self.assertEqual(self.count("SELECT count(*) FROM command WHERE kind = 'water' AND (payload->>'amount_ml')::float = 25"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'watering.planned'"), 1)
        worker.watering_tick({self.plant_id: self.now}, self.now + timedelta(minutes=1))     # 1시간 안에는 다시 넣지 않는다
        worker.watering_tick({}, self.now + timedelta(minutes=2))                            # hub 를 다시 켜도 (last_run 이 비어도)
        self.assertEqual(self.count("SELECT count(*) FROM command"), 1)

    def test_below_ratio_is_skipped(self):
        self.readings(12)
        self.watered(4)
        worker.watering_tick({}, self.now)
        with self.connect() as conn:                                                 # 줄 시각은 지났는데 실제로는 덜 말랐다
            conn.execute(
                """UPDATE growth_state SET posterior = jsonb_set(posterior, '{watering,plan,water_at}', to_jsonb(%s::text))
                   WHERE plant_id = %s""",
                ((self.now - timedelta(minutes=5)).isoformat(), self.plant_id),
            )
        self.watered(0.5)                                                            # 30분 전에 사람이 물을 줬다
        done = worker.watering_tick({self.plant_id: self.now}, self.now + timedelta(seconds=30))
        self.assertEqual(done[0]["watering"], "skipped")
        self.assertEqual(self.count("SELECT count(*) FROM command"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'watering.skipped'"), 1)

    def test_failed_command_is_recorded_once_and_not_retried(self):
        self.readings(12)
        self.watered(6)
        worker.watering_tick({}, self.now)
        worker.watering_tick({self.plant_id: self.now}, self.now + timedelta(seconds=30))
        with self.connect() as conn:
            conn.execute("""UPDATE command SET status = 'failed', result = '{"code": "TANK_EMPTY"}', done_at = now()""")
        for minutes in (1, 2):
            worker.watering_tick({self.plant_id: self.now}, self.now + timedelta(minutes=minutes))
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'watering.failed'"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM command"), 1)

    def test_no_recent_readings_means_no_prediction(self):
        done = worker.watering_tick({}, self.now)
        self.assertEqual([d["method"] for d in done if d["plant_id"] == self.plant_id], ["none"])
        self.assertIsNone(self.state()["watering"]["plan"])
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'watering.no_data'"), 1)

    def test_no_data_is_recorded_once_per_outage(self):
        for hours in range(4):                                                       # 4시간 내내 끊겨 있어도
            worker.watering_tick({}, self.now + timedelta(hours=hours))
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'watering.no_data'"), 1)

    def due_plan(self):
        """줄 시각이 이미 지난 급수 계획을 만든다 (6시간 동안 마름 > 기준치 300)."""
        self.readings(12)
        self.watered(6)
        worker.watering_tick({}, self.now)

    def test_foresee_failure_retries_after_10_minutes(self):
        self.due_plan()
        with patch("hub.worker.run_foresee", side_effect=RuntimeError("foresee 고장")):
            for seconds in (30, 60, 90):                                             # 30초마다 다시 돌리지 않는다
                worker.watering_tick({self.plant_id: self.now}, self.now + timedelta(seconds=seconds))
            errors = "SELECT count(*) FROM event WHERE kind = 'growth.error'"
            self.assertEqual(self.count(errors), 1)
            worker.watering_tick({self.plant_id: self.now}, self.now + timedelta(minutes=11))     # 10분 뒤에는 다시
            self.assertEqual(self.count(errors), 2)

    def test_post_failure_respects_cooldown(self):
        self.due_plan()
        calls = []

        def broken(plant_id, request_id, ml):
            calls.append(request_id)
            raise OSError("서버에 닿지 않음")

        with patch("hub.worker.post_command", broken):
            for seconds in (30, 60, 90):
                worker.watering_tick({self.plant_id: self.now}, self.now + timedelta(seconds=seconds))
        self.assertEqual(len(calls), 1)                                              # 실패해도 1시간은 다시 넣지 않는다
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'watering.failed'"), 1)

    def test_watering_refresh_keeps_growth_model(self):
        self.readings(48)
        with self.connect() as conn:
            for day in range(3, 9):
                conn.execute("INSERT INTO scan (id, plant_id, ts, day, height_cm, status) VALUES (%s, %s, %s, %s, %s, 'processing')",
                             (uuid4(), self.plant_id, self.now - timedelta(days=10 - day), day, 10 + day))
        worker.run_once({})
        with patch.dict(os.environ, {"WATER_THRESHOLD": "400"}):                      # 급수 설정만 바뀌었다
            worker.watering_tick({}, self.now)
        self.assertEqual(self.count("SELECT count(*) FROM model_version WHERE status = 'active'"), 1)
        body = self.client.get(f"/api/v1/plants/{self.plant_id}/growth").json()
        self.assertEqual(body["status"], "available")                                # 생장 그래프가 비지 않는다


if __name__ == "__main__":
    unittest.main()
