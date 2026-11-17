import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import security
from app.main import app
from tests.postgres_case import PostgresIntegrationCase


UPLOAD_URL = "/api/v1/ingest/sensor-readings"
DAY = "2026-11-17"


@unittest.skipUnless(
    os.getenv("RUN_SENSOR_HISTORY_DB_TESTS") == "1",
    "RUN_SENSOR_HISTORY_DB_TESTS=1로 실행하세요.",
)
class SensorHistoryIntegrationTests(PostgresIntegrationCase):
    def setUp(self):
        super().setUp()
        for service in ("sensor", "sensor_history"):
            self.enterContext(patch(f"app.services.{service}.get_connection", self.connect))
        self.enterContext(patch.object(security, "DEVICE_UPLOAD_TOKEN", "test-device-token"))
        self.client = self.enterContext(TestClient(app))
        self.url = f"/api/v1/plants/{self.plant_id}/sensor-readings"

    @staticmethod
    def ts(clock):
        return f"{DAY}T{clock}:00+09:00"

    def upload(self, *readings):
        response = self.client.post(
            UPLOAD_URL,
            json={"items": [{"plant_id": self.plant_id, **item} for item in readings]},
            headers={"Authorization": "Bearer test-device-token"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return [item["id"] for item in response.json()["results"]]

    def test_ten_minute_uses_valid_values_and_skips_empty_buckets(self):
        with self.connect() as conn:
            other_plant = conn.execute(
                "INSERT INTO plant (name, species, sown_at) VALUES ('다른 화분', 'test', now()) RETURNING id",
            ).fetchone()[0]

        self.upload(
            {"ts": self.ts("09:02"), "air_temp_c": 99},  # before from
            {"ts": self.ts("09:03"), "air_temp_c": 20, "light_level": 2},
            {"ts": self.ts("09:05"), "air_temp_c": 30, "rh_pct": 60, "light_level": 4, "lux": 80},
            {"ts": self.ts("09:09"), "rh_pct": 70},
            {"ts": self.ts("09:20")},
            {"ts": self.ts("09:23"), "air_temp_c": 99},  # at to
            {"plant_id": other_plant, "ts": self.ts("09:04"), "air_temp_c": 99},
        )
        params = {"from": self.ts("09:03"), "to": self.ts("09:23"), "limit": 1}
        first = self.client.get(self.url, params=params)
        self.assertEqual(first.status_code, 200, first.text)
        page = first.json()
        self.assertEqual(page["interval"], "10m")
        self.assertEqual(page["next_offset"], 1)
        bucket, = page["items"]
        self.assertEqual(datetime.fromisoformat(bucket["ts"]), datetime.fromisoformat(self.ts("09:00")))
        self.assertEqual(bucket["sample_count"], 3)
        self.assertEqual((bucket["air_temp_c"], bucket["rh_pct"], bucket["light_level"]), (25, 65, 4))
        self.assertEqual(bucket["lux"], 80)
        self.assertIsNone(bucket["soil_moisture_pct"])
        self.assertEqual(bucket["valid_counts"], {
            "air_temp_c": 2, "rh_pct": 2, "soil_moisture_pct": 0, "lux": 1,
            "ppfd": 0, "vpd_kpa": 0, "light_level": 2,
        })
        self.assertNotIn("id", bucket)

        response = self.client.get(self.url, params={**params, "offset": page["next_offset"]})
        self.assertEqual(response.status_code, 200, response.text)
        last = response.json()
        only_nulls, = last["items"]
        self.assertEqual(datetime.fromisoformat(only_nulls["ts"]), datetime.fromisoformat(self.ts("09:20")))
        self.assertEqual(only_nulls["sample_count"], 1)
        for field, count in only_nulls["valid_counts"].items():
            self.assertIsNone(only_nulls[field])
            self.assertEqual(count, 0)
        self.assertIsNone(last["next_offset"])

    def test_raw_preserves_readings_and_pages_by_row(self):
        later_id, earlier_id = self.upload(
            {"ts": self.ts("09:08"), "air_temp_c": 24.3, "rh_pct": 65, "light_level": 3},
            {"ts": self.ts("09:06"), "air_temp_c": 22, "rh_pct": 60},
        )
        params = {"from": self.ts("09:00"), "to": self.ts("09:10"), "interval": "raw", "limit": 1}
        response = self.client.get(self.url, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        first = response.json()
        self.assertEqual(first["interval"], "raw")
        earlier, = first["items"]
        self.assertEqual(earlier["id"], earlier_id)
        self.assertIsNone(earlier["light_level"])
        self.assertNotIn("sample_count", earlier)
        self.assertNotIn("valid_counts", earlier)
        self.assertEqual(first["next_offset"], 1)

        response = self.client.get(self.url, params={
            **params, "from": first["from"], "to": first["to"], "offset": first["next_offset"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        last = response.json()
        later, = last["items"]
        self.assertEqual(later["id"], later_id)
        self.assertEqual(datetime.fromisoformat(later["ts"]), datetime.fromisoformat(self.ts("09:08")))
        self.assertEqual((later["air_temp_c"], later["rh_pct"], later["light_level"]), (24.3, 65, 3))
        self.assertIsNone(last["next_offset"])

    def test_defaults_and_validation(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["interval"], body["limit"], body["offset"]), ("10m", 100, 0))
        self.assertEqual(body["items"], [])
        self.assertIsNone(body["next_offset"])
        self.assertEqual(
            datetime.fromisoformat(body["to"]) - datetime.fromisoformat(body["from"]),
            timedelta(hours=24),
        )
        for params in (
            {"from": self.ts("09:00")},
            {"from": self.ts("09:00"), "to": "2026-12-19T09:00:00+09:00"},
            {"from": self.ts("10:00"), "to": self.ts("09:00")},
            {"from": f"{DAY}T09:00:00", "to": self.ts("10:00")},
            {"interval": "5m"}, {"limit": 1001}, {"offset": -1},
        ):
            with self.subTest(params=params):
                error = self.client.get(self.url, params=params)
                self.assertEqual(error.status_code, 422, error.text)
                self.assertEqual(error.json()["error"]["code"], "VALIDATION_ERROR")

        missing = self.client.get("/api/v1/plants/99999/sensor-readings")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "PLANT_NOT_FOUND")
