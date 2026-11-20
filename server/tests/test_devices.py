import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import security
from app.main import app
from tests.postgres_case import PostgresIntegrationCase


URL = "/api/v1/ingest/device-states"
COMMANDS = "/api/v1/commands"
INITIAL = "2026-11-16T09:00:00Z"
LATER = "2026-11-16T09:05:00Z"


@unittest.skipUnless(
    os.getenv("RUN_DEVICE_DB_TESTS") == "1",
    "장비 상태 통합 테스트를 실행하려면 RUN_DEVICE_DB_TESTS=1을 지정하세요.",
)
class DeviceIntegrationTests(PostgresIntegrationCase):
    def setUp(self):
        super().setUp()
        for service in ("devices", "commands", "overview"):
            self.enterContext(patch(f"app.services.{service}.get_connection", self.connect))
        self.enterContext(patch.object(security, "DEVICE_UPLOAD_TOKEN", "test-device-token"))
        self.enterContext(patch.dict(os.environ, {
            "COMMAND_PASSWORD": "test-command-password",
            "CONTROL_EDGE_NAME": "edge",
        }))
        self.client = self.enterContext(TestClient(app))
        self.headers = {"Authorization": "Bearer test-device-token"}

    def report(self, *items):
        return self.client.post(URL, json={"items": items}, headers=self.headers)

    def statuses(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        result, = response.json()["results"]
        return result["state_status"], result["settings_status"]

    def overview(self):
        response = self.client.get(f"/api/v1/plants/{self.plant_id}/overview")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def settings(self, revision, mode="predictive"):
        return {
            "applied_settings": {
                "watering_mode": mode, "soil_lower_pct": 40, "soil_target_pct": 55,
            },
            "settings_revision": revision,
            "settings_applied_at": INITIAL,
        }

    def test_batch_order_same_time_duplicate_and_overview(self):
        cam = {"name": "cam1", "role": "camera", "online": None, "ts": INITIAL}
        edge = {"name": "edge", "role": "edge", "online": True, "ts": INITIAL}
        response = self.report(edge, cam)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [
            {"index": 0, "name": "edge", "state_status": "created", "settings_status": "not_provided"},
            {"index": 1, "name": "cam1", "state_status": "created", "settings_status": "not_provided"},
        ])
        self.assertEqual(
            self.statuses(self.report({**cam, "ts": "2026-11-16T18:00:00+09:00"})),
            ("duplicate", "not_provided"),
        )
        overview = self.overview()
        self.assertEqual([device["name"] for device in overview["devices"]], ["cam1", "edge"])
        self.assertIsNone(overview["devices"][0]["online"])
        self.assertIsNone(overview["settings"])

    def test_state_and_settings_advance_independently(self):
        edge = {"name": "edge", "role": "edge", "online": True, "ts": INITIAL}
        initial = {**self.settings(2), "firmware": "1.0", "optional": "previous"}
        self.assertEqual(self.statuses(self.report({**edge, "info": initial})), ("created", "updated"))
        self.assertEqual(
            self.statuses(self.report({**edge, "ts": LATER, "online": False, "info": {"optional": None}})),
            ("updated", "not_provided"),
        )
        revision = {**self.settings(3, "off"), "firmware": "stale"}
        self.assertEqual(self.statuses(self.report({**edge, "info": revision})), ("ignored_stale", "updated"))
        self.assertEqual(
            self.statuses(self.report({**edge, "ts": "2026-11-16T09:10:00Z", "online": False, "info": self.settings(1)})),
            ("updated", "ignored_stale"),
        )
        self.assertEqual(
            self.statuses(self.report({
                **edge, "ts": "2026-11-16T09:10:00Z", "online": False,
                "info": {**self.settings(3, "off"), "settings_applied_at": "2026-11-16T18:00:00+09:00"},
            })),
            ("duplicate", "duplicate"),
        )
        overview = self.overview()
        self.assertFalse(overview["devices"][0]["online"])
        self.assertEqual(overview["settings"]["applied_settings"], self.settings(3, "off")["applied_settings"])
        with self.connect() as conn:
            info = conn.execute("SELECT info FROM device WHERE name = 'edge'").fetchone()[0]
        self.assertEqual(info["firmware"], "1.0")
        self.assertIsNone(info["optional"])

    def test_conflict_rolls_back_entire_batch(self):
        edge = {"name": "edge", "role": "edge", "online": True, "ts": INITIAL}
        cam = {"name": "cam0", "role": "camera", "online": None, "ts": INITIAL}
        self.assertEqual(self.statuses(self.report({**edge, "info": {**self.settings(2), "ready": True}})),
                         ("created", "updated"))

        examples = (
            ({**edge, "role": "guard", "ts": LATER}, "CONTENT_CONFLICT"),
            ({**edge, "online": False}, "CONTENT_CONFLICT"),
            ({**edge, "info": {"ready": 1}}, "CONTENT_CONFLICT"),
            ({**edge, "ts": LATER, "info": self.settings(2, "off")}, "SETTINGS_REVISION_CONFLICT"),
            ({**edge, "info": {**self.settings(2), "settings_applied_at": LATER}}, "SETTINGS_REVISION_CONFLICT"),
        )
        for item, code in examples:
            with self.subTest(code=code, item=item):
                response = self.report(item, cam)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertEqual(response.json()["error"]["details"][0]["index"], 0)
        self.assertEqual([device["name"] for device in self.overview()["devices"]], ["edge"])

    def test_invalid_reports_are_rejected(self):
        edge = {"name": "edge", "role": "edge", "online": True, "ts": INITIAL}
        self.assertEqual(self.client.post(URL, json={"items": [edge]}).status_code, 401)

        invalid_batches = (
            (edge, edge),
            ({**edge, "info": None},),
            ({**edge, "info": {"settings_revision": 1}},),
            ({**edge, "name": "cam0", "role": "camera", "info": self.settings(1)},),
            ({**edge, "ts": "2026-11-16T09:00:00"},),
            ({k: v for k, v in edge.items() if k != "online"},),
        )
        for batch in invalid_batches:
            with self.subTest(batch=batch):
                self.assertEqual(self.report(*batch).status_code, 422)
        self.assertEqual(self.overview()["devices"], [])

    def test_command_result_shares_settings_snapshot(self):
        edge = {"name": "edge", "role": "edge", "online": True, "ts": INITIAL}
        self.assertEqual(self.statuses(self.report({**edge, "info": self.settings(2, "off")})), ("created", "updated"))
        response = self.client.post(
            COMMANDS,
            json={
                "request_id": str(uuid4()), "plant_id": self.plant_id,
                "kind": "set_watering", "payload": {"mode": "off"},
            },
            headers={"X-Command-Password": "test-command-password"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        command_id = response.json()["id"]
        claimed = self.client.post(f"{COMMANDS}/claim", json={"plant_id": self.plant_id}, headers=self.headers)
        self.assertEqual(claimed.json()["commands"][0]["id"], command_id)
        response = self.client.post(
            f"{COMMANDS}/{command_id}/result",
            json={
                "status": "done",
                "result": {"finished_at": INITIAL, "message": "설정 적용", "device_name": "edge", **self.settings(2, "off")},
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["settings_status"], "duplicate")
