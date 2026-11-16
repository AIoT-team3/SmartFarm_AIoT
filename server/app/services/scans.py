import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from app.database import get_connection
from app.errors import APIError
from app.schemas.scans import FileKind, ScanRegistration, ScanSubmission, SuccessfulSubmission
from app.services.scan_files import (
    StagedScanFile,
    file_digest,
    scan_file_path,
    scan_storage_root,
    stage_scan_file,
)


KOREA = ZoneInfo("Asia/Seoul")


@contextmanager
def scan_cursor():
    try:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            yield cur
    except psycopg.OperationalError as exc:
        raise APIError(503, "STORAGE_UNAVAILABLE", "DB에 일시적으로 접근할 수 없습니다.") from exc
    except psycopg.Error as exc:
        raise APIError(500, "INTERNAL_SERVER_ERROR", "스캔 처리 중 오류가 발생했습니다.") from exc


def register_scan(request: ScanRegistration) -> dict:
    with scan_cursor() as cur:
        cur.execute("SELECT id, plant_id, ts, day, status FROM scan WHERE id = %s", (request.id,))
        existing = cur.fetchone()
        if existing is not None:
            return _registered_duplicate(request, existing)

        cur.execute("SELECT sown_at FROM plant WHERE id = %s FOR KEY SHARE", (request.plant_id,))
        plant = cur.fetchone()
        if plant is None:
            raise APIError(
                404, "PLANT_NOT_FOUND", "등록되지 않은 화분입니다.", [{"field": "plant_id"}],
            )
        day = (request.ts.astimezone(KOREA).date() - plant["sown_at"].astimezone(KOREA).date()).days
        if day < 0:
            raise APIError(
                422, "VALIDATION_ERROR", "촬영일은 파종일보다 앞설 수 없습니다.", [{"field": "ts"}],
            )

        cur.execute(
            """
            INSERT INTO scan (id, plant_id, ts, day)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            RETURNING id, plant_id, day, status
            """,
            (request.id, request.plant_id, request.ts, day),
        )
        scan = cur.fetchone()
        if scan is not None:
            return {**scan, "duplicate": False}

        cur.execute("SELECT id, plant_id, ts, day, status FROM scan WHERE id = %s", (request.id,))
        existing = cur.fetchone()
        if existing is None:
            raise APIError(503, "STORAGE_UNAVAILABLE", "기존 스캔 등록을 확인할 수 없습니다.")
        return _registered_duplicate(request, existing)


def _registered_duplicate(request: ScanRegistration, existing: dict) -> dict:
    if (request.plant_id, request.ts) != (existing["plant_id"], existing["ts"]):
        raise APIError(409, "CONTENT_CONFLICT", "같은 스캔 ID에 다른 화분 또는 촬영 시각이 있습니다.")
    return {**existing, "duplicate": True}


def _lock_scan(cur, scan_id: UUID) -> dict:
    # 파일 연결과 제출은 같은 스캔 행을 잠근 상태에서 처리한다.
    cur.execute("SELECT status, cloud_file FROM scan WHERE id = %s FOR UPDATE", (scan_id,))
    scan = cur.fetchone()
    if scan is None:
        raise APIError(404, "SCAN_NOT_FOUND", "등록되지 않은 스캔입니다.")
    return scan


def upload_scan_file(
    scan_id: UUID,
    kind: FileKind,
    source: BinaryIO,
    cam: str | None,
    angle_idx: int | None,
    sha256: str | None,
) -> dict:
    if kind == "cloud":
        if cam is not None or angle_idx is not None:
            raise APIError(
                422, "VALIDATION_ERROR", "점군에는 카메라 이름과 각도 순번을 보내지 마세요.",
            )
    elif cam is None or angle_idx is None:
        raise APIError(
            422, "VALIDATION_ERROR", "원본·마스크에는 카메라 이름과 각도 순번이 필요합니다.",
            [{"field": field} for field, value in (("cam", cam), ("angle_idx", angle_idx)) if value is None],
        )

    root = scan_storage_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="upload-", dir=root) as temporary:
            staged = stage_scan_file(source, Path(temporary) / "file", kind, sha256)
            with scan_cursor() as cur:
                return _connect_uploaded_file(cur, root, scan_id, kind, cam, angle_idx, staged)
    except OSError as exc:
        raise APIError(503, "STORAGE_UNAVAILABLE", "스캔 파일 저장 공간에 접근할 수 없습니다.") from exc


def _connect_uploaded_file(
    cur, root: Path, scan_id: UUID, kind: FileKind, cam: str | None,
    angle_idx: int | None, staged: StagedScanFile,
) -> dict:
    scan = _lock_scan(cur, scan_id)
    image_id = None
    image = None
    stored_path = scan["cloud_file"] if kind == "cloud" else None
    if kind != "cloud":
        cur.execute(
            "SELECT id, file, mask FROM image WHERE scan_id = %s AND cam = %s AND angle_idx = %s",
            (scan_id, cam, angle_idx),
        )
        image = cur.fetchone()
        if image is not None:
            image_id = image["id"]
            stored_path = image["file" if kind == "image" else "mask"]

    if stored_path is not None:
        if file_digest(root / stored_path) != staged.digest:
            raise APIError(409, "CONTENT_CONFLICT", "이미 연결된 파일과 내용이 다릅니다.")
        return _file_response(scan_id, kind, cam, angle_idx, image_id, "duplicate", stored_path, staged)

    if scan["status"] != "pending":
        raise APIError(409, "INVALID_SCAN_STATE", "제출이 끝난 스캔에는 새 파일을 추가할 수 없습니다.")
    if kind == "mask" and image is None:
        raise APIError(404, "IMAGE_NOT_FOUND", "마스크를 연결할 원본 사진이 없습니다.")

    relative_path = scan_file_path(scan_id, kind, cam, angle_idx, staged.extension)
    if kind == "image":
        cur.execute(
            """
            INSERT INTO image (scan_id, cam, angle_idx, file, sha256, w, h)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (scan_id, cam, angle_idx, relative_path, staged.digest, staged.width, staged.height),
        )
        image_id = cur.fetchone()["id"]
    elif kind == "mask":
        cur.execute("UPDATE image SET mask = %s WHERE id = %s", (relative_path, image_id))
    else:
        cur.execute("UPDATE scan SET cloud_file = %s WHERE id = %s", (relative_path, scan_id))

    # 파일 교체가 실패하면 DB 변경도 함께 롤백된다. 커밋 실패 뒤 재시도는 같은 위치를 사용한다.
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged.path, destination)
    return _file_response(scan_id, kind, cam, angle_idx, image_id, "created", relative_path, staged)


def _file_response(
    scan_id: UUID, kind: FileKind, cam: str | None, angle_idx: int | None,
    image_id: int | None, status: str, path: str, staged: StagedScanFile,
) -> dict:
    return {
        "scan_id": scan_id,
        "kind": kind,
        "cam": cam,
        "angle_idx": angle_idx,
        "image_id": image_id,
        "status": status,
        "url": f"/files/{path}",
        "w": staged.width,
        "h": staged.height,
    }


def submit_scan(scan_id: UUID, submission: ScanSubmission) -> dict:
    with scan_cursor() as cur:
        scan = _lock_scan(cur, scan_id)
        if scan["status"] != "pending":
            return {"scan_id": scan_id, "status": scan["status"], "duplicate": True}

        if isinstance(submission, SuccessfulSubmission):
            _validate_uploaded_files(cur, scan_id, scan["cloud_file"], submission)
            cur.execute(
                """
                UPDATE scan
                SET status = 'processing', height_cm = %s, canopy_cm = %s, volume_cm3 = %s
                WHERE id = %s
                """,
                (submission.height_cm, submission.canopy_cm, submission.volume_cm3, scan_id),
            )
            status = "processing"
        else:
            cur.execute("UPDATE scan SET status = 'failed' WHERE id = %s", (scan_id,))
            status = "failed"
        return {"scan_id": scan_id, "status": status, "duplicate": False}


def _validate_uploaded_files(cur, scan_id: UUID, cloud_file: str | None, submission: SuccessfulSubmission):
    expected = {(image.cam, image.angle_idx): image.mask_uploaded for image in submission.images}
    if not expected or len(expected) != len(submission.images):
        raise APIError(
            422, "VALIDATION_ERROR", "사진 목록은 한 장 이상이며 카메라·각도 조합이 고유해야 합니다.",
            [{"field": "images"}],
        )

    for field in ("height_cm", "canopy_cm", "volume_cm3"):
        value = getattr(submission, field)
        if value is not None and value < 0:
            raise APIError(
                422, "VALIDATION_ERROR", "측정값은 0 이상이어야 합니다.", [{"field": field}],
            )

    cur.execute("SELECT cam, angle_idx, file, mask FROM image WHERE scan_id = %s", (scan_id,))
    images = cur.fetchall()
    actual = {(image["cam"], image["angle_idx"]): image["mask"] is not None for image in images}
    paths = [path for image in images for path in (image["file"], image["mask"]) if path is not None]
    if cloud_file is not None:
        paths.append(cloud_file)
    root = scan_storage_root()
    if (
        expected != actual
        or submission.cloud_uploaded != (cloud_file is not None)
        or not all((root / path).is_file() for path in paths)
    ):
        raise APIError(
            409, "FILES_INCOMPLETE", "제출한 사진·마스크·점군 목록과 실제 업로드 파일이 일치하지 않습니다.",
        )
