import os
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from tests.postgres_case import PostgresIntegrationCase


@unittest.skipUnless(os.getenv("RUN_GROWTH_DB_TESTS") == "1", "RUN_GROWTH_DB_TESTS=1로 실행하세요.")
class GrowthIntegrationTests(PostgresIntegrationCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("app.services.growth.get_connection", self.connect))
        self.client = self.enterContext(TestClient(app))
        self.url = f"/api/v1/plants/{self.plant_id}/growth"

    def model(self, conn, status="active", kind="growth"):
        return conn.execute(
            "INSERT INTO model_version (kind, status) VALUES (%s, %s) RETURNING id",
            (kind, status),
        ).fetchone()[0]

    def scan(self, conn, scan_id=None, *, ts="2026-11-09T00:00:00Z", plant_id=None):
        scan_id = scan_id or uuid4()
        conn.execute(
            "INSERT INTO scan (id, plant_id, ts, day) VALUES (%s, %s, %s, 10)",
            (scan_id, plant_id or self.plant_id, ts),
        )
        return scan_id

    def prediction(self, conn, model, scan, day, *, score=None, band=None):
        bounds = (10, 14) if band is not None else (None, None)
        prediction_id = conn.execute(
            """
            INSERT INTO prediction (model_id, origin_scan_id, origin_day, target_day, height_mu, p10, p90)
            VALUES (%s, %s, 10, %s, 12, %s, %s)
            RETURNING id
            """,
            (model, scan, day, *bounds),
        ).fetchone()[0]
        if score is not None:
            conn.execute(
                """
                INSERT INTO backtest
                    (prediction_id, origin_day, target_day, horizon, predicted, observed, abs_err, in_band)
                VALUES (%s, 10, %s, %s, 12, %s, %s, %s)
                """,
                (prediction_id, day, day - 10, 12 + score, score, band),
            )
        return prediction_id

    def test_default_model_scan_dates_and_scoped_scores(self):
        with self.connect() as conn:
            conn.execute(
                "UPDATE plant SET sown_at = %s WHERE id = %s",
                ("2026-10-29T15:30:00Z", self.plant_id),
            )
            old_model = self.model(conn)
            model = self.model(conn)
            self.model(conn, status="candidate")
            self.model(conn, kind="perceive")
            low_scan = self.scan(conn, UUID(int=1))
            high_scan = self.scan(conn, UUID(int=2))
            older_scan = self.scan(conn, UUID(int=3), ts="2026-11-08T00:00:00Z")
            self.scan(conn, ts="2026-11-10T00:00:00Z")
            self.prediction(conn, model, low_scan, 11, score=90)
            self.prediction(conn, model, older_scan, 11, score=90)
            self.prediction(conn, old_model, high_scan, 11, score=91)
            no_score = self.prediction(conn, model, high_scan, 14)
            self.prediction(conn, model, high_scan, 13, score=2)
            self.prediction(conn, model, high_scan, 12, score=5, band=False)
            scored = self.prediction(conn, model, high_scan, 11, score=1, band=True)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "available")
        self.assertEqual(body["model"], {"id": model, "kind": "growth", "status": "active"})
        self.assertEqual(body["origin_scan"]["id"], str(high_scan))
        rows = body["predictions"]
        self.assertEqual([row["target_day"] for row in rows], [11, 12, 13, 14])
        self.assertEqual(rows[0]["target_date"], "2026-11-10")
        self.assertEqual(rows[0]["backtest"], {
            "prediction_id": scored, "horizon": 1, "predicted": 12,
            "observed": 13, "abs_err": 1, "in_band": True,
        })
        self.assertIsNone(rows[2]["backtest"]["in_band"])
        self.assertEqual(rows[3]["id"], no_score)
        for field in ("backtest", "p10", "p90", "pairs_mu"):
            self.assertIsNone(rows[3][field])
        summary = body["backtest_summary"]
        self.assertEqual(summary["evaluated_count"], 3)
        self.assertAlmostEqual(summary["mae_cm"], 8 / 3)
        self.assertEqual(summary["band_evaluated_count"], 2)
        self.assertEqual(summary["band_hit_rate"], 0.5)
        self.assertIsNone(body["growth_state"])
        self.assertEqual(body["harvest"], {
            "status": "not_available", "estimated_date": None, "remaining_days": None,
        })

    def test_explicit_historical_prediction_and_latest_growth_state(self):
        with self.connect() as conn:
            old_model = self.model(conn, status="archived")
            current_model = self.model(conn)
            old_scan = self.scan(conn)
            new_scan = self.scan(conn, ts="2026-11-10T00:00:00Z")
            prediction = self.prediction(conn, old_model, old_scan, 11)
            self.prediction(conn, old_model, new_scan, 11)
            conn.execute(
                """
                INSERT INTO growth_state (plant_id, model_id, posterior, anomaly_streak)
                VALUES (%s, %s, '{"internal": 1}', 2)
                """,
                (self.plant_id, current_model),
            )

        response = self.client.get(self.url, params={"model_id": old_model, "origin_scan_id": old_scan})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["model"]["status"], "archived")
        self.assertEqual(body["origin_scan"]["id"], str(old_scan))
        self.assertEqual([row["id"] for row in body["predictions"]], [prediction])
        state = body["growth_state"]
        self.assertEqual(state["model_id"], current_model)
        self.assertEqual(state["anomaly_streak"], 2)
        self.assertIsNone(state["regime_start"])
        self.assertIn("updated_at", state)
        self.assertNotIn("posterior", state)

    def test_empty_results_keep_nullable_values(self):
        with self.connect() as conn:
            self.model(conn, kind="perceive")
            scan = self.scan(conn)
        response = self.client.get(self.url, params={"origin_scan_id": scan})
        self.assertEqual(response.status_code, 200, response.text)
        no_model = response.json()
        self.assertEqual(no_model["status"], "no_model")
        self.assertIsNone(no_model["model"])
        self.assertIsNone(no_model["origin_scan"])

        with self.connect() as conn:
            model = self.model(conn)
        automatic = self.client.get(self.url)
        explicit = self.client.get(self.url, params={"model_id": model, "origin_scan_id": scan})
        self.assertIsNone(automatic.json()["origin_scan"])
        self.assertEqual(explicit.json()["origin_scan"]["id"], str(scan))
        for response in (automatic, explicit):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "no_prediction")
            self.assertEqual(response.json()["model"]["id"], model)

        for body in (no_model, automatic.json(), explicit.json()):
            self.assertEqual(body["predictions"], [])
            self.assertEqual(body["backtest_summary"], {
                "evaluated_count": 0, "mae_cm": None, "band_evaluated_count": 0, "band_hit_rate": None,
            })

    def test_rejects_invalid_ids_model_kind_and_scan_ownership(self):
        with self.connect() as conn:
            model = self.model(conn)
            perceive = self.model(conn, kind="perceive")
            other_plant = conn.execute(
                "INSERT INTO plant (name, species, sown_at) VALUES ('다른 화분', 'test', now()) RETURNING id"
            ).fetchone()[0]
            other_scan = self.scan(conn, plant_id=other_plant)
        cases = [
            ("/api/v1/plants/500/growth", {}, 404, "PLANT_NOT_FOUND"),
            ("/api/v1/plants/0/growth", {}, 422, "VALIDATION_ERROR"),
            (self.url, {"model_id": 500}, 404, "MODEL_NOT_FOUND"),
            (self.url, {"model_id": 2**63}, 422, "VALIDATION_ERROR"),
            (self.url, {"model_id": perceive}, 422, "VALIDATION_ERROR"),
            (self.url, {"origin_scan_id": str(uuid4())}, 404, "SCAN_NOT_FOUND"),
            (self.url, {"origin_scan_id": "invalid"}, 422, "VALIDATION_ERROR"),
            (self.url, {"model_id": model, "origin_scan_id": str(other_scan)}, 422, "VALIDATION_ERROR"),
        ]
        for url, params, status, code in cases:
            with self.subTest(url=url, params=params):
                response = self.client.get(url, params=params)
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json()["error"]["code"], code)
