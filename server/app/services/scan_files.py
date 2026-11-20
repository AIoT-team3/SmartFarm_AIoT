import hashlib
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from app.errors import APIError
from app.schemas.scans import FileKind


CHUNK_SIZE = 1024 * 1024
PLY_HEADER_LIMIT = 64 * 1024
DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parents[2] / ".scan-files"


@dataclass(frozen=True)
class StagedScanFile:
    path: Path
    digest: str
    extension: str
    width: int | None = None
    height: int | None = None


def scan_storage_root() -> Path:
    return Path(os.getenv("SCAN_STORAGE_ROOT", str(DEFAULT_STORAGE_ROOT))).expanduser()


def _max_file_bytes() -> int | None:
    configured_limit = os.getenv("SCAN_MAX_FILE_BYTES")
    if not configured_limit:
        return None
    try:
        limit = int(configured_limit)
        if limit > 0:
            return limit
    except ValueError:
        pass
    raise APIError(500, "INTERNAL_SERVER_ERROR", "스캔 파일 크기 제한 설정을 확인하세요.")


def stage_scan_file(
    source: BinaryIO, target: Path, kind: FileKind, expected_digest: str | None,
) -> StagedScanFile:
    limit = _max_file_bytes()
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as output:
        while chunk := source.read(CHUNK_SIZE):
            size += len(chunk)
            if limit is not None and size > limit:
                raise APIError(413, "PAYLOAD_TOO_LARGE", "스캔 파일 크기 제한을 초과했습니다.")
            digest.update(chunk)
            output.write(chunk)
    if size == 0:
        raise APIError(422, "VALIDATION_ERROR", "빈 파일은 업로드할 수 없습니다.", [{"field": "file"}])
    sha256 = digest.hexdigest()
    if expected_digest is not None and expected_digest.lower() != sha256:
        raise APIError(
            422, "VALIDATION_ERROR", "파일 내용이 입력한 SHA-256과 다릅니다.",
            [{"field": "sha256"}],
        )

    if kind == "cloud":
        _validate_ply(target)
        return StagedScanFile(target, sha256, "ply")
    extension, width, height = _inspect_image(target, kind)
    return StagedScanFile(target, sha256, extension, width, height)


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def scan_file_path(
    scan_id: UUID, kind: FileKind, cam: str | None, angle_idx: int | None, extension: str,
) -> str:
    if kind == "cloud":
        return f"scans/{scan_id}/cloud.ply"
    # 일반 카메라 이름은 그대로 쓰고, 특수 문자가 있는 이름은 별도 이름 공간에 저장한다.
    if cam is None or angle_idx is None:
        raise ValueError("Camera and angle are required for images and masks.")
    camera = cam if re.fullmatch(r"[A-Za-z0-9_-]+", cam) else "~" + hashlib.sha256(cam.encode()).hexdigest()
    folder = "images" if kind == "image" else "masks"
    return f"scans/{scan_id}/{folder}/{camera}-{angle_idx}.{extension}"


def _inspect_image(path: Path, kind: FileKind) -> tuple[str, int, int]:
    extensions = {"JPEG": "jpg", "PNG": "png"} if kind == "image" else {"PNG": "png"}
    with path.open("rb") as source:
        signature = source.read(8)
    if not signature.startswith(b"\x89PNG\r\n\x1a\n") and not signature.startswith(b"\xff\xd8\xff"):
        raise _unsupported_format(kind)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=("JPEG", "PNG")) as image:
                if image.format not in extensions:
                    raise _unsupported_format(kind)
                extension = extensions[image.format]
                width, height = image.size
                image.verify()
            # JPEG 구조 검사에서 놓칠 수 있는 잘린 픽셀 데이터도 검출한다.
            with Image.open(path, formats=("JPEG", "PNG")) as image:
                image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise APIError(
            422, "VALIDATION_ERROR", "사진 파일을 읽을 수 없습니다.", [{"field": "file"}],
        ) from exc
    return extension, width, height


def _validate_ply(path: Path) -> None:
    with path.open("rb") as source:
        header = source.read(PLY_HEADER_LIMIT)
    if not header.startswith((b"ply\n", b"ply\r\n", b"ply\r")):
        raise _unsupported_format("cloud")

    # 점군의 형식·헤더·정점 선언만 확인한다. 좌표 해석은 분석 단계에서 한다.
    lines = re.split(rb"\r\n|\n|\r", header)
    valid_formats = {b"ascii", b"binary_little_endian", b"binary_big_endian"}
    encoding = lines[1].split()
    end = next((index for index, line in enumerate(lines) if line.strip() == b"end_header"), None)
    if end is not None and end < len(lines) - 1 and encoding[:1] == [b"format"]:
        if len(encoding) == 3 and encoding[1] in valid_formats and encoding[2] == b"1.0":
            vertices = [
                fields[2] for line in lines[2:end]
                if len(fields := line.split()) == 3 and fields[:2] == [b"element", b"vertex"]
            ]
            if len(vertices) == 1 and vertices[0].isdigit():
                if not vertices[0].strip(b"0") or any(lines[end + 1:]):
                    return
    raise APIError(
        422, "VALIDATION_ERROR", "PLY 점군 헤더 또는 데이터가 올바르지 않습니다.", [{"field": "file"}],
    )


def _unsupported_format(kind: FileKind) -> APIError:
    supported = {"image": "JPEG 또는 PNG", "mask": "PNG", "cloud": "PLY"}
    return APIError(
        415, "UNSUPPORTED_MEDIA_TYPE", f"{supported[kind]} 파일만 지원합니다.", [{"field": "file"}],
    )


def scan_file_url(relative_path: str) -> str:
    return f"/files/{relative_path}"
