import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.types.json import Jsonb

from app.database import get_connection
from app.main import app
from app import security


BASE = "/api/v1/commands"
FINISHED = "2026-11-16T17:00:00+09:00"


@unittest.skipUnless(
    os.getenv("RUN_COMMAND_DB_TESTS") == "1",
    "명령 통합 테스트를 실행하려면 RUN_COMMAND_DB_TESTS=1을 지정하세요.",
)
class CommandIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.schema = f"command_tests_{uuid4().hex}"
        with get_connection() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)

        ddl = Path(__file__).resolve().parents[1] / "db" / "tables_setting.sql"
        schema_sql = sql.Identifier(self.schema).as_string()
        source = ddl.read_text()
        expected = "SET LOCAL search_path = public;"
        self.assertEqual(source.count(expected), 1, "테스트 스키마 지정 위치를 확인하세요.")
        isolated_ddl = source.replace(expected, f"SET LOCAL search_path = {schema_sql};")
        with self.connect() as conn:
            conn.execute(isolated_ddl, prepare=False)
            self.plant_id = conn.execute(
                "INSERT INTO plant (name, species, sown_at) VALUES ('명령 테스트', 'test', now()) RETURNING id"
            ).fetchone()[0]

        self.enterContext(patch("app.services.commands.get_connection", self.connect))
        self.enterContext(patch.object(security, "DEVICE_UPLOAD_TOKEN", "test-device-token"))
        self.enterContext(patch.dict(os.environ, {
            "COMMAND_PASSWORD": "test-command-password",
            "CONTROL_EDGE_NAME": "edge",
        }))
        self.client = self.enterContext(TestClient(app))
        self.dashboard = {"X-Command-Password": "test-command-password"}
        self.edge = {"Authorization": "Bearer test-device-token"}

    def connect(self):
        conn = get_connection()
        try:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            conn.commit()
        except Exception:
            conn.close()
            raise
        return conn

    def drop_schema(self):
        with get_connection() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def command_payload(self, kind, payload):
        return {
            "request_id": str(uuid4()),
            "plant_id": self.plant_id,
            "kind": kind,
            "payload": payload,
        }

    def create(self, kind, payload):
        response = self.client.post(BASE, json=self.command_payload(kind, payload), headers=self.dashboard)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def claim(self, **kwargs):
        response = self.client.post(
            f"{BASE}/claim", json={"plant_id": self.plant_id, **kwargs}, headers=self.edge,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["commands"]

    def report(self, command_id, result, status="done"):
        return self.client.post(
            f"{BASE}/{command_id}/result", json={"status": status, "result": result}, headers=self.edge,
        )

    def test_water_lifecycle_and_retries(self):
        payload = self.command_payload("water", {"amount_ml": 20})
        initial = self.client.post(BASE, json=payload, headers=self.dashboard)
        self.assertEqual(initial.status_code, 201, initial.text)
        command_id = initial.json()["id"]
        self.assertEqual(initial.json()["status"], "pending")

        result = {"finished_at": FINISHED, "message": "급수 완료", "amount_ml": 19.5}
        refused = self.report(command_id, result)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["error"]["code"], "INVALID_COMMAND_STATE")

        first_claim, = self.claim()
        repeated_claim, = self.claim()
        self.assertEqual(first_claim, repeated_claim)
        self.assertEqual(first_claim["status"], "picked")
        ttl = datetime.fromisoformat(first_claim["expires_at"]) - datetime.fromisoformat(first_claim["created_at"])
        self.assertEqual(ttl.total_seconds(), 120)

        completed = self.report(command_id, result).json()
        repeat = self.report(command_id, {
            **result, "finished_at": "2026-11-16T08:00:00Z", "actuation_id": None,
        }).json()
        self.assertEqual(completed["status"], "done")
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(repeat["done_at"], completed["done_at"])
        conflict = self.report(command_id, {**result, "amount_ml": 10})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "CONTENT_CONFLICT")
        self.assertEqual(self.claim(), [])

        retry = self.client.post(BASE, json=payload, headers=self.dashboard)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual((retry.json()["id"], retry.json()["status"]), (command_id, "done"))
        view = self.client.get(f"{BASE}/{command_id}")
        self.assertEqual(view.status_code, 200)
        self.assertEqual(view.json()["result"]["amount_ml"], 19.5)

    def test_concurrent_create_keeps_one_record_and_rejects_changed_content(self):
        payload = self.command_payload("water", {"amount_ml": 20})
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(
                lambda _: self.client.post(BASE, json=payload, headers=self.dashboard),
                range(2),
            ))
        self.assertEqual(sorted(response.status_code for response in replies), [200, 201])
        self.assertEqual(len({response.json()["id"] for response in replies}), 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM command").fetchone()[0], 1)
        changed = self.client.post(
            BASE, json={**payload, "payload": {"amount_ml": 21}}, headers=self.dashboard,
        )
        self.assertEqual(changed.status_code, 409)

    def test_expiration_only_closes_pending_commands(self):
        picked = self.create("water", {"amount_ml": 5})
        self.assertEqual(self.claim()[0]["id"], picked)
        expired = self.create("water", {"amount_ml": 5})
        scan = self.create("scan_now", None)
        with self.connect() as conn:
            conn.execute("UPDATE command SET created_at = now() - interval '130 seconds'")
        delivered = {command["id"]: command for command in self.claim()}
        self.assertEqual(delivered.keys(), {picked, scan})
        ttl = datetime.fromisoformat(delivered[scan]["expires_at"]) - datetime.fromisoformat(delivered[scan]["created_at"])
        self.assertEqual(ttl.total_seconds(), 900)
        response = self.client.get(f"{BASE}/{expired}").json()
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["result"]["code"], "COMMAND_EXPIRED")
        self.assertEqual(
            datetime.fromisoformat(response["done_at"]),
            datetime.fromisoformat(response["result"]["finished_at"]),
        )
        failed = self.report(scan, {
            "finished_at": FINISHED, "message": "촬영 중", "code": "SCAN_BUSY",
        }, status="failed")
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(self.client.get(f"{BASE}/{scan}").json()["status"], "failed")

    def test_scan_completion_needs_scan_id_but_not_an_uploaded_scan(self):
        scan_command = self.create("scan_now", None)
        self.claim()
        result = {"finished_at": FINISHED, "message": "촬영 완료"}
        self.assertEqual(self.report(scan_command, result).status_code, 422)
        scan_id = str(uuid4())
        completed = self.report(scan_command, {**result, "scan_id": scan_id})
        self.assertEqual(completed.status_code, 200, completed.text)
        view = self.client.get(f"{BASE}/{scan_command}").json()
        self.assertEqual(view["result"]["scan_id"], scan_id)

    def test_settings_revision_and_conflict(self):
        reverse = self.create("set_watering", {"mode": "off"})
        latest = self.create("set_watering", {
            "mode": "threshold", "lower_pct": 40, "target_pct": 55,
        })
        conflicting = self.create("set_watering", {"mode": "off"})
        self.claim()
        result = {
            "finished_at": FINISHED,
            "message": "설정 적용 완료",
            "device_name": "edge",
            "applied_settings": {
                "watering_mode": "threshold", "soil_lower_pct": 40, "soil_target_pct": 55,
            },
            "settings_revision": 2,
            "settings_applied_at": FINISHED,
        }
        missing = self.report(latest, result)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "DEVICE_NOT_FOUND")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO device (name, role, info) VALUES ('edge', 'edge', %s)",
                (Jsonb({"other": "preserved"}),),
            )
        self.assertEqual(self.report(latest, result).json()["settings_status"], "updated")
        self.assertEqual(self.report(latest, result).json()["settings_status"], "duplicate")
        off = {**result["applied_settings"], "watering_mode": "off"}
        self.assertEqual(self.report(reverse, {
            **result, "applied_settings": off, "settings_revision": 1,
        }).json()["settings_status"], "ignored_stale")
        conflict = self.report(conflicting, {**result, "applied_settings": off})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "SETTINGS_REVISION_CONFLICT")
        self.assertEqual(self.client.get(f"{BASE}/{conflicting}").json()["status"], "picked")
        with self.connect() as conn:
            info = conn.execute("SELECT info FROM device WHERE name = 'edge'").fetchone()[0]
        self.assertEqual(info["applied_settings"]["watering_mode"], "threshold")
        self.assertEqual(info["settings_revision"], 2)
        self.assertEqual(info["other"], "preserved")

    def test_auth_validation_and_estop_priority(self):
        payload = self.command_payload("water", {"amount_ml": 5})
        self.assertEqual(self.client.post(BASE, json=payload).status_code, 401)
        self.assertEqual(self.client.post(f"{BASE}/claim", json={"plant_id": self.plant_id}).status_code, 401)
        invalid_amount = self.client.post(
            BASE, json={**payload, "payload": {"amount_ml": "5"}}, headers=self.dashboard,
        )
        self.assertEqual(invalid_amount.status_code, 422)
        self.assertEqual(self.client.get(f"{BASE}/9223372036854775808").status_code, 422)
        self.assertEqual(self.client.get(f"{BASE}/1.0").status_code, 422)
        self.assertEqual(self.client.get(f"{BASE}/99999").status_code, 404)

        self.create("water", {"amount_ml": 5})
        estop = self.create("estop", {"enabled": True})
        self.assertEqual(self.claim(limit=1)[0]["id"], estop)
        rejected = self.report(estop, {
            "finished_at": FINISHED, "message": "상태 확인", "enabled": False,
        })
        self.assertEqual(rejected.status_code, 422)
        done = self.report(estop, {
            "finished_at": FINISHED, "message": "정지 확인", "enabled": True,
        })
        self.assertEqual(done.status_code, 200, done.text)
