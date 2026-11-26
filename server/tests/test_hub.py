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


if __name__ == "__main__":
    unittest.main()
