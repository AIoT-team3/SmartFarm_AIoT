import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import security
from app.main import app
from tests.postgres_case import PostgresIntegrationCase


URL = "/api/v1/ingest/actuations"
TS = "2026-11-16T18:00:00+09:00"


@unittest.skipUnless(os.getenv("RUN_ACTUATION_DB_TESTS") == "1", "RUN_ACTUATION_DB_TESTS=1로 실행하세요.")
class ActuationIntegrationTests(PostgresIntegrationCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("app.services.actuations.get_connection", self.connect))
        self.enterContext(patch.object(security, "DEVICE_UPLOAD_TOKEN", "test-device-token"))
        self.client = self.enterContext(TestClient(app))
        self.headers = {"Authorization": "Bearer test-device-token"}

    def record(self, **fields):
        return {
            "id": str(uuid4()), "plant_id": self.plant_id, "ts": TS,
            "kind": "water", "ok": True, "source": "auto", **fields,
        }

    def send(self, *items):
        return self.client.post(URL, json={"items": items}, headers=self.headers)

    def count(self):
        with self.connect() as conn:
            return conn.execute("SELECT count(*) FROM actuation").fetchone()[0]

    def test_auto_watering_without_soil_and_failed_record_are_stored(self):
        succeeded = self.record(amount=20, unit="mL", context={"air_temp_c": 24.3, "rh_pct": 61.2})
        failed = self.record(ok=False, reason="장치가 급수를 거절함")
        response = self.send(failed, succeeded)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["results"], [
            {"index": 0, "id": failed["id"], "status": "created"},
            {"index": 1, "id": succeeded["id"], "status": "created"},
        ])
        with self.connect() as conn:
            rows = {
                str(row_id): (amount, unit, ok, context)
                for row_id, amount, unit, ok, context in conn.execute(
                    "SELECT id, amount, unit, ok, context FROM actuation"
                )
            }
        self.assertEqual(rows[succeeded["id"]], (20, "mL", True, succeeded["context"]))
        self.assertEqual(rows[failed["id"]], (None, None, False, None))

    def test_retry_normalizes_times_nulls_and_json_key_order(self):
        record = self.record(context={"control_mode": "threshold", "threshold_pct": 42, "custom": True})
        self.assertEqual(self.send(record).status_code, 200)
        retry = self.send({
            **record, "ts": "2026-11-16T09:00:00Z", "amount": None, "unit": None, "reason": None,
            "context": {"custom": True, "threshold_pct": 42.0, "control_mode": "threshold"},
        })
        self.assertEqual(retry.json()["results"][0]["status"], "duplicate")
        conflict = self.send({**record, "context": {**record["context"], "custom": 1}})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "CONTENT_CONFLICT")
        self.assertEqual(self.count(), 1)

    def test_content_conflict_rolls_back_other_records_in_batch(self):
        first = self.record(id="ffffffff-ffff-4fff-8fff-ffffffffffff")
        new = self.record(id="00000000-0000-4000-8000-000000000001", kind="light")
        self.assertEqual(self.send(first).status_code, 200)
        conflict = self.send({**first, "ok": False}, new)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["details"][0]["index"], 0)
        self.assertEqual(self.count(), 1)

    def test_request_validation_and_unknown_plant(self):
        record = self.record()
        self.assertEqual(self.client.post(URL, json={"items": [record]}).status_code, 401)
        self.assertEqual(self.send(record, record).status_code, 422)
        unknown = self.send(record, self.record(plant_id=99999))
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["error"]["code"], "PLANT_NOT_FOUND")
        for fields in (
            {"amount": 20}, {"amount": 20, "unit": "L"}, {"amount": -1, "unit": "mL"},
            {"ok": 1}, {"context": {"measured_soil_pct": 101}},
            {"context": {"forecast_hours": 0}}, {"context": {"threshold_pct": None}},
        ):
            with self.subTest(fields=fields):
                self.assertEqual(self.send({**record, **fields}).status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_concurrent_retries_keep_one_record(self):
        record = self.record()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.send(record), range(2)))
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(
            sorted(response.json()["results"][0]["status"] for response in responses),
            ["created", "duplicate"],
        )
        self.assertEqual(self.count(), 1)
