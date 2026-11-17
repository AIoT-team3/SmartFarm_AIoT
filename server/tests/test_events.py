import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import security
from app.main import app
from tests.postgres_case import PostgresIntegrationCase


URL = "/api/v1/ingest/events"
TS = "2026-11-16T18:00:00+09:00"


@unittest.skipUnless(os.getenv("RUN_EVENT_DB_TESTS") == "1", "RUN_EVENT_DB_TESTS=1로 실행하세요.")
class EventIntegrationTests(PostgresIntegrationCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("app.services.events.get_connection", self.connect))
        self.enterContext(patch.object(security, "DEVICE_UPLOAD_TOKEN", "test-device-token"))
        self.client = self.enterContext(TestClient(app))
        self.headers = {"Authorization": "Bearer test-device-token"}

    def record(self, **fields):
        return {
            "id": str(uuid4()),
            "plant_id": self.plant_id,
            "ts": TS,
            "kind": "sensor_timeout",
            "level": "warning",
            "message": "온습도 센서값이 수신되지 않았습니다.",
            **fields,
        }

    def upload(self, *items):
        return self.client.post(URL, json={"items": items}, headers=self.headers)

    def count(self):
        with self.connect() as conn:
            return conn.execute("SELECT count(*) FROM event").fetchone()[0]

    def test_distinct_ids_save_same_time_and_kind_in_input_order(self):
        first = self.record(id="ffffffff-ffff-4fff-8fff-ffffffffffff", data={"elapsed_minutes": 20})
        second = self.record(id="00000000-0000-4000-8000-000000000001")
        response = self.upload(first, second)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["results"], [
            {"index": 0, "id": first["id"], "status": "created"},
            {"index": 1, "id": second["id"], "status": "created"},
        ])
        with self.connect() as conn:
            rows = conn.execute("SELECT id::text, level, message, data FROM event").fetchall()
        self.assertCountEqual(rows, [
            (first["id"], "warning", first["message"], first["data"]),
            (second["id"], "warning", second["message"], None),
        ])

    def test_retries_accept_equivalent_payload_and_reject_changed_data(self):
        first = self.record(data={"nested": {"enabled": True, "elapsed": 20}})
        second = self.record()
        self.assertEqual(self.upload(first, second).status_code, 200)
        response = self.upload(
            {**first, "ts": "2026-11-16T09:00:00Z", "data": {"nested": {"elapsed": 20, "enabled": True}}},
            {**second, "data": None},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["status"] for row in response.json()["results"]], ["duplicate", "duplicate"])
        conflict = self.upload({**first, "data": {"nested": {"enabled": 1, "elapsed": 20}}})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "CONTENT_CONFLICT")
        self.assertEqual(self.count(), 2)

    def test_unknown_plant_or_conflict_rolls_back_other_records(self):
        existing = self.record(id="ffffffff-ffff-4fff-8fff-ffffffffffff")
        new = self.record(id="00000000-0000-4000-8000-000000000001")
        self.assertEqual(self.upload(existing).status_code, 200)

        unknown = self.upload(new, self.record(plant_id=99999))
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["error"]["code"], "PLANT_NOT_FOUND")
        self.assertEqual(unknown.json()["error"]["details"], [{"index": 1, "field": "plant_id"}])
        conflict = self.upload({**existing, "message": "다른 사건"}, new)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["details"], [{"index": 0, "field": "id"}])
        self.assertEqual(self.count(), 1)

    def test_validation_and_authentication(self):
        item = self.record()
        self.assertEqual(self.client.post(URL, json={"items": [item]}).status_code, 401)
        self.assertEqual(self.upload(item, item).status_code, 422)
        self.assertEqual(self.upload().status_code, 422)
        for invalid in (
            {"id": "wrong"}, {"plant_id": "1"}, {"ts": "2026-11-16T18:00:00"},
            {"kind": "x" * 51}, {"level": "critical"}, {"message": "   "},
            {"data": ["not an object"]}, {"data": {"nested": "bad\x00"}},
        ):
            with self.subTest(invalid=invalid):
                response = self.upload({**item, **invalid})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.count(), 0)

    def test_concurrent_retries_store_one_row(self):
        item = self.record()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(self.upload, (item, item)))
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
        self.assertCountEqual(
            [response.json()["results"][0]["status"] for response in responses],
            ["created", "duplicate"],
        )
        self.assertEqual(self.count(), 1)
