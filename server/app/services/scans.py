import os
import stat
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from app.database import get_connection
from app.errors import APIError
from app.schemas.scans import FileKind, ScanRegistration, ScanStatus, ScanSubmission, SuccessfulSubmission
from app.services.scan_files import (
    StagedScanFile,
    file_digest,
    scan_file_path,
    scan_file_url,
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
        "url": scan_file_url(path),
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


def get_scan_detail(scan_id: UUID) -> dict:
    with scan_cursor() as cur:
        cur.execute(
            """
            SELECT id, plant_id, ts, day, status, height_cm, canopy_cm, volume_cm3,
                   leaf_pairs_est, leaf_area_est, leaf_pairs_manual, cloud_file
            FROM scan
            WHERE id = %s
            """,
            (scan_id,),
        )
        scan = cur.fetchone()
        if scan is None:
            raise APIError(404, "SCAN_NOT_FOUND", "등록되지 않은 스캔입니다.")

        cur.execute(
            """
            SELECT id, cam, angle_idx, file, mask, w, h
            FROM image
            WHERE scan_id = %s
            ORDER BY cam, angle_idx
            """,
            (scan_id,),
        )
        images = cur.fetchall()

    cloud_file = scan.pop("cloud_file")
    scan["cloud_url"] = scan_file_url(cloud_file) if cloud_file is not None else None
    for image in images:
        image["url"] = scan_file_url(image.pop("file"))
        mask = image.pop("mask")
        image["mask_url"] = scan_file_url(mask) if mask is not None else None
    scan["images"] = images
    return scan


def get_published_scan_file(file_path: str) -> tuple[Path, str, os.stat_result]:
    missing = APIError(404, "FILE_NOT_FOUND", "요청한 파일을 제공할 수 없습니다.")
    parts = file_path.split("/")
    if (
        len(parts) not in (3, 4) or parts[0] != "scans"
        or any(part in ("", ".", "..") for part in parts)
        or "\\" in file_path or "\0" in file_path
    ):
        raise missing
    try:
        scan_id = UUID(parts[1])
    except ValueError as exc:
        raise missing from exc

    if len(parts) == 3 and parts[2] == "cloud.ply":
        media_type = "application/octet-stream"
        query = "SELECT 1 FROM scan WHERE id = %s AND cloud_file = %s"
    elif len(parts) == 4 and parts[2] == "images" and parts[3].endswith((".jpg", ".png")):
        media_type = "image/jpeg" if parts[3].endswith(".jpg") else "image/png"
        query = "SELECT 1 FROM image WHERE scan_id = %s AND file = %s"
    elif len(parts) == 4 and parts[2] == "masks" and parts[3].endswith(".png"):
        media_type = "image/png"
        query = "SELECT 1 FROM image WHERE scan_id = %s AND mask = %s"
    else:
        raise missing

    with scan_cursor() as cur:
        cur.execute(query, (scan_id, file_path))
        if cur.fetchone() is None:
            raise missing

    try:
        root = scan_storage_root().resolve()
        path = (root / file_path).resolve(strict=True)
        if not path.is_relative_to(root):
            raise missing
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise missing
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise missing from exc
    except OSError as exc:
        raise APIError(503, "STORAGE_UNAVAILABLE", "스캔 파일 저장소에 접근할 수 없습니다.") from exc

    return path, media_type, file_stat


def list_scans(
    plant_id: int,
    from_time: datetime | None,
    to_time: datetime | None,
    status: ScanStatus | None,
    limit: int,
    offset: int,
) -> dict:
    if from_time is None and to_time is None:
        to_time = datetime.now(timezone.utc)
        from_time = to_time - timedelta(days=1)
    elif from_time is None or to_time is None:
        missing = "from" if from_time is None else "to"
        raise APIError(
            422, "VALIDATION_ERROR", "from과 to는 함께 입력하세요.", [{"field": missing}],
        )

    if from_time >= to_time:
        raise APIError(
            422, "VALIDATION_ERROR", "from은 to보다 앞서야 합니다.", [{"field": "from"}],
        )
    if to_time - from_time > timedelta(days=31):
        raise APIError(
            422, "VALIDATION_ERROR", "한 번에 최대 31일까지 조회할 수 있습니다.", [{"field": "to"}],
        )

    with scan_cursor() as cur:
        cur.execute("SELECT 1 FROM plant WHERE id = %s", (plant_id,))
        if cur.fetchone() is None:
            raise APIError(404, "PLANT_NOT_FOUND", "등록되지 않은 화분입니다.", [{"field": "plant_id"}])

        cur.execute(
            """
            SELECT scan.id, scan.ts, scan.day, scan.status, scan.height_cm, scan.canopy_cm,
                   scan.volume_cm3, scan.leaf_pairs_est, scan.leaf_area_est, scan.leaf_pairs_manual,
                   (SELECT count(*) FROM image WHERE image.scan_id = scan.id) AS image_count
            FROM scan
            WHERE scan.plant_id = %s AND scan.ts >= %s AND scan.ts < %s
              AND (%s::text IS NULL OR scan.status = %s)
            ORDER BY scan.ts DESC, scan.id DESC
            LIMIT %s OFFSET %s
            """,
            (plant_id, from_time, to_time, status, status, limit + 1, offset),
        )
        rows = cur.fetchall()

    return {
        "plant_id": plant_id,
        "from": from_time,
        "to": to_time,
        "items": rows[:limit],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if len(rows) > limit else None,
    }
