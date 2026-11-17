import hashlib
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from app import security
from app.main import app
from tests.postgres_case import PostgresIntegrationCase


BASE = "/api/v1/scans"
CAPTURED = "2026-12-01T00:01:00+09:00"
PLY = b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n"


@unittest.skipUnless(os.getenv("RUN_SCAN_DB_TESTS") == "1", "RUN_SCAN_DB_TESTS=1로 실행하세요.")
class ScanIntegrationTests(PostgresIntegrationCase):
    def setUp(self):
        super().setUp()
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {
            "SCAN_STORAGE_ROOT": str(self.root),
            "SCAN_MAX_FILE_BYTES": "",
        }))
        self.enterContext(patch("app.services.scans.get_connection", self.connect))
        self.enterContext(patch.object(security, "DEVICE_UPLOAD_TOKEN", "test-device-token"))
        self.client = self.enterContext(TestClient(app))
        self.headers = {"Authorization": "Bearer test-device-token"}
        with self.connect() as conn:
            conn.execute(
                "UPDATE plant SET sown_at = %s WHERE id = %s",
                ("2026-11-30T23:59:00+09:00", self.plant_id),
            )

    @staticmethod
    def image_bytes(color="green", fmt="PNG"):
        with BytesIO() as buffer:
            Image.new("RGB", (9, 7), color=color).save(buffer, format=fmt)
            return buffer.getvalue()

    def registration(self):
        return {"id": str(uuid4()), "plant_id": self.plant_id, "ts": CAPTURED}

    def register(self):
        payload = self.registration()
        response = self.client.post(BASE, json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return payload

    def upload(self, scan_id, kind, content, **fields):
        form = {"kind": kind, **fields}
        if kind != "cloud":
            form.setdefault("cam", "cam0")
            form.setdefault("angle_idx", "0")
        return self.client.post(
            f"{BASE}/{scan_id}/files",
            data=form,
            files={"file": ("sample", content, "application/octet-stream")},
            headers=self.headers,
        )

    def submit(self, scan_id, payload):
        return self.client.post(f"{BASE}/{scan_id}/submission", json=payload, headers=self.headers)

    def scan_row(self, scan_id):
        with self.connect() as conn:
            return conn.execute("SELECT status, height_cm, cloud_file FROM scan WHERE id = %s", (scan_id,)).fetchone()

    def test_registration_uses_korean_dates_and_checks_retries(self):
        payload = self.registration()
        self.assertEqual(self.client.post(BASE, json=payload).status_code, 401)
        created = self.client.post(BASE, json=payload, headers=self.headers)
        self.assertEqual(created.json(), {
            "id": payload["id"], "plant_id": self.plant_id, "day": 1,
            "status": "pending", "duplicate": False,
        })
        retry = self.client.post(
            BASE, json={**payload, "ts": "2026-11-30T15:01:00Z"}, headers=self.headers,
        )
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json()["duplicate"])
        conflict = self.client.post(
            BASE, json={**payload, "ts": "2026-12-01T00:02:00+09:00"}, headers=self.headers,
        )
        self.assertEqual(conflict.status_code, 409)
        earlier = self.client.post(
            BASE, json={**self.registration(), "ts": "2026-11-29T00:00:00+09:00"}, headers=self.headers,
        )
        self.assertEqual(earlier.status_code, 422)
        missing = self.client.post(
            BASE, json={**self.registration(), "plant_id": 99999}, headers=self.headers,
        )
        self.assertEqual(missing.status_code, 404)

    def test_full_upload_checks_inventory_and_closes_scan(self):
        payload = self.register()
        scan_id = payload["id"]
        raw = self.image_bytes(fmt="JPEG")
        mask = self.image_bytes("white")
        missing = self.upload(scan_id, "mask", mask)
        self.assertEqual(missing.json()["error"]["code"], "IMAGE_NOT_FOUND")

        original = self.upload(scan_id, "image", raw, sha256=hashlib.sha256(raw).hexdigest().upper())
        self.assertEqual(original.status_code, 201, original.text)
        image_response = original.json()
        self.assertEqual((image_response["w"], image_response["h"]), (9, 7))
        self.assertEqual((self.root / image_response["url"].removeprefix("/files/")).read_bytes(), raw)
        self.assertEqual(self.upload(scan_id, "image", raw).status_code, 200)
        self.assertEqual(self.upload(scan_id, "image", self.image_bytes("blue")).status_code, 409)

        submission = {
            "outcome": "success", "images": [{"cam": "cam0", "angle_idx": 0, "mask_uploaded": True}],
            "cloud_uploaded": True, "height_cm": 12.4, "volume_cm3": 210.5,
        }
        self.assertEqual(self.submit(scan_id, submission).json()["error"]["code"], "FILES_INCOMPLETE")
        self.assertEqual(self.scan_row(scan_id)[0], "pending")
        uploaded_mask = self.upload(scan_id, "mask", mask)
        self.assertEqual(uploaded_mask.status_code, 201, uploaded_mask.text)
        self.assertEqual(uploaded_mask.json()["image_id"], image_response["image_id"])
        cloud = self.upload(scan_id, "cloud", PLY)
        self.assertEqual(cloud.status_code, 201, cloud.text)
        self.assertIsNone(cloud.json()["image_id"])
        self.assertIsNone(cloud.json()["w"])
        self.assertEqual(self.upload(scan_id, "cloud", PLY).status_code, 200)

        result = self.submit(scan_id, submission)
        self.assertEqual(result.status_code, 202, result.text)
        self.assertEqual(result.json(), {"scan_id": scan_id, "status": "processing", "duplicate": False})
        retry = self.submit(scan_id, {**submission, "height_cm": 99, "cloud_uploaded": False})
        self.assertEqual(retry.json(), {"scan_id": scan_id, "status": "processing", "duplicate": True})
        self.assertEqual(self.scan_row(scan_id)[:2], ("processing", 12.4))
        again = self.client.post(BASE, json=payload, headers=self.headers)
        self.assertEqual(again.json()["status"], "processing")
        self.assertEqual(self.upload(scan_id, "image", raw).status_code, 200)
        new_slot = self.upload(scan_id, "image", raw, angle_idx="1")
        self.assertEqual(new_slot.json()["error"]["code"], "INVALID_SCAN_STATE")

    def test_photo_only_submission_checks_exact_keys_and_masks(self):
        scan_id = self.register()["id"]
        raw = self.image_bytes()
        self.assertEqual(self.upload(scan_id, "image", raw).status_code, 201)
        self.assertEqual(self.upload(scan_id, "image", raw, cam="cam1").status_code, 201)
        cam0 = {"cam": "cam0", "angle_idx": 0, "mask_uploaded": False}
        cam1 = {"cam": "cam1", "angle_idx": 0, "mask_uploaded": False}
        submission = {"outcome": "success", "images": [cam0, cam1], "cloud_uploaded": False}
        self.assertEqual(self.submit(scan_id, {**submission, "images": [cam0]}).status_code, 409)
        self.assertEqual(self.submit(scan_id, {**submission, "images": [cam0, cam0]}).status_code, 422)
        self.assertEqual(self.submit(scan_id, {**submission, "height_cm": -1}).status_code, 422)
        self.assertEqual(self.submit(scan_id, submission).status_code, 202)
        self.assertEqual(self.scan_row(scan_id), ("processing", None, None))

    def test_failed_report_closes_scan_and_retry_keeps_failed(self):
        scan_id = self.register()["id"]
        failed = {"outcome": "failed", "error": {"code": "CAMERA_TIMEOUT", "message": "응답 시간 초과"}}
        invalid = self.submit(scan_id, {**failed, "images": []})
        self.assertEqual(invalid.status_code, 422)
        result = self.submit(scan_id, failed)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"scan_id": scan_id, "status": "failed", "duplicate": False})
        duplicate = self.submit(scan_id, {"outcome": "success", "images": [], "cloud_uploaded": False})
        self.assertEqual(duplicate.json(), {"scan_id": scan_id, "status": "failed", "duplicate": True})
        self.assertEqual(self.submit(scan_id, {"outcome": "unknown"}).status_code, 422)

    def test_upload_validation_and_configurable_size_limit(self):
        scan_id = self.register()["id"]
        raw = self.image_bytes()
        self.assertEqual(self.upload(str(uuid4()), "image", raw).status_code, 404)
        self.assertEqual(self.upload(scan_id, "image", raw, sha256="0" * 64).status_code, 422)
        self.assertEqual(self.upload(scan_id, "image", b"plain text").status_code, 415)
        self.assertEqual(self.upload(scan_id, "image", raw[:12]).status_code, 422)
        self.assertEqual(self.upload(scan_id, "cloud", PLY, cam="cam0").status_code, 422)
        self.assertEqual(self.upload(scan_id, "cloud", b"ply\nformat ascii 1.0\n").status_code, 422)
        url = f"{BASE}/{scan_id}/files"
        self.assertEqual(self.client.post(url, json={}, headers=self.headers).status_code, 415)
        pair = [("file", ("one.png", raw)), ("file", ("two.png", raw))]
        self.assertEqual(
            self.client.post(url, data={"kind": "image"}, files=pair, headers=self.headers).status_code, 422,
        )
        with patch.dict(os.environ, {"SCAN_MAX_FILE_BYTES": "4"}):
            self.assertEqual(self.upload(scan_id, "image", raw).status_code, 413)

    def test_concurrent_uploads_keep_one_row_and_file(self):
        scan_id = self.register()["id"]
        raw = self.image_bytes()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.upload(scan_id, "image", raw), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 201])
        self.assertEqual(len({response.json()["image_id"] for response in responses}), 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM image").fetchone()[0], 1)

    def test_file_write_failure_rolls_back_image_connection(self):
        scan_id = self.register()["id"]
        with patch("app.services.scans.os.replace", side_effect=OSError("test write failure")):
            response = self.upload(scan_id, "image", self.image_bytes())
        self.assertEqual(response.status_code, 503)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM image").fetchone()[0], 0)
        self.assertEqual(self.upload(scan_id, "image", self.image_bytes()).status_code, 201)

    def test_scan_detail_without_uploaded_files_and_failed_scan(self):
        scan_id = self.register()["id"]
        url = f"{BASE}/{scan_id}"
        detail = self.client.get(url)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["images"], [])
        self.assertIsNone(detail.json()["cloud_url"])
        self.assertIsNone(detail.json()["height_cm"])
        self.assertEqual(detail.json()["status"], "pending")
        self.assertEqual(self.client.get(f"{BASE}/invalid-uuid").status_code, 422)
        missing = self.client.get(f"{BASE}/{uuid4()}")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "SCAN_NOT_FOUND")

        self.submit(scan_id, {"outcome": "failed", "error": {"code": "CAMERA_TIMEOUT", "message": "촬영 실패"}})
        failed = self.client.get(url)
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["status"], "failed")

    def test_scan_detail_urls_serve_uploaded_jpeg_png_and_ply(self):
        scan_id = self.register()["id"]
        jpeg = self.image_bytes(fmt="JPEG")
        png = self.image_bytes("white")
        other = self.upload(scan_id, "image", png, cam="cam1").json()
        last_angle = self.upload(scan_id, "image", jpeg, cam="cam0", angle_idx="10").json()
        original = self.upload(scan_id, "image", jpeg, cam="cam0", angle_idx="2").json()
        mask = self.upload(scan_id, "mask", png, cam="cam0", angle_idx="2").json()
        cloud = self.upload(scan_id, "cloud", PLY).json()
        result = self.submit(scan_id, {
            "outcome": "success", "cloud_uploaded": True, "height_cm": 12.4,
            "images": [
                {"cam": "cam0", "angle_idx": 2, "mask_uploaded": True},
                {"cam": "cam0", "angle_idx": 10, "mask_uploaded": False},
                {"cam": "cam1", "angle_idx": 0, "mask_uploaded": False},
            ],
        })
        self.assertEqual(result.status_code, 202)
        detail_response = self.client.get(f"{BASE}/{scan_id}")
        self.assertEqual(detail_response.status_code, 200, detail_response.text)
        detail = detail_response.json()
        self.assertEqual(detail["status"], "processing")
        self.assertEqual(detail["height_cm"], 12.4)
        self.assertEqual(detail["cloud_url"], cloud["url"])
        self.assertEqual(detail["images"], [
            {"id": original["image_id"], "cam": "cam0", "angle_idx": 2, "url": original["url"], "mask_url": mask["url"], "w": 9, "h": 7},
            {"id": last_angle["image_id"], "cam": "cam0", "angle_idx": 10, "url": last_angle["url"], "mask_url": None, "w": 9, "h": 7},
            {"id": other["image_id"], "cam": "cam1", "angle_idx": 0, "url": other["url"], "mask_url": None, "w": 9, "h": 7},
        ])
        for url, content_type, expected in (
            (detail["images"][0]["url"], "image/jpeg", jpeg),
            (detail["images"][0]["mask_url"], "image/png", png),
            (detail["images"][2]["url"], "image/png", png),
            (detail["cloud_url"], "application/octet-stream", PLY),
        ):
            with self.subTest(url=url):
                file = self.client.get(url)
                self.assertEqual(file.status_code, 200)
                self.assertEqual(file.headers["content-type"], content_type)
                self.assertEqual(file.content, expected)

    def test_file_delivery_rejects_unpublished_missing_and_outside_paths(self):
        scan_id = self.register()["id"]
        original = self.upload(scan_id, "image", self.image_bytes()).json()
        path = self.root / original["url"].removeprefix("/files/")
        unrelated = path.with_name("unlisted-0.png")
        unrelated.write_bytes(b"unpublished file")
        settings = self.root / ".env"
        settings.write_text("test fixture")
        self.assertEqual(
            self.client.get(original["url"].replace(path.name, unrelated.name)).status_code, 404,
        )
        self.assertEqual(self.client.get("/files/.env").status_code, 404)
        self.assertEqual(self.client.get(f"/files/scans/{scan_id}/images/%2e%2e/cloud.ply").status_code, 404)

        path.unlink()
        self.assertEqual(self.client.get(original["url"]).status_code, 404)
        external = Path(self.enterContext(TemporaryDirectory())) / "outside.png"
        external.write_bytes(b"outside storage")
        path.symlink_to(external)
        self.assertEqual(self.client.get(original["url"]).status_code, 404)
