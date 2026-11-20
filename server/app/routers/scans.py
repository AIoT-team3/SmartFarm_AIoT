from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile

from app.errors import APIError
from app.schemas.scans import (
    MAX_ANGLE_INDEX,
    FileKind,
    ScanFileResponse,
    ScanRegistration,
    ScanRegistrationResponse,
    ScanSubmission,
    ScanSubmissionResponse,
)
from app.security import require_json, verify_device_token
from app.services.scans import register_scan, submit_scan, upload_scan_file


router = APIRouter(prefix="/api/v1/scans", tags=["스캔"], dependencies=[Depends(verify_device_token)])


async def require_multipart(request: Request) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "multipart/form-data":
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type은 multipart/form-data여야 합니다.")
    form = await request.form()
    if len(form.getlist("file")) > 1:
        raise APIError(422, "VALIDATION_ERROR", "한 요청에는 파일 하나만 보내세요.", [{"field": "file"}])


@router.post(
    "",
    status_code=201,
    response_model=ScanRegistrationResponse,
    dependencies=[Depends(require_json)],
    summary="스캔 등록",
    responses={
        200: {"model": ScanRegistrationResponse, "description": "동일 스캔 등록 재전송"},
        404: {"description": "등록되지 않은 화분"},
        409: {"description": "같은 스캔 ID의 입력 충돌"},
    },
)
def create_scan(payload: ScanRegistration, response: Response):
    result = register_scan(payload)
    if result["duplicate"]:
        response.status_code = 200
    return result


@router.post(
    "/{scan_id}/files",
    status_code=201,
    response_model=ScanFileResponse,
    dependencies=[Depends(require_multipart)],
    summary="스캔 파일 업로드",
    responses={
        200: {"model": ScanFileResponse, "description": "동일 파일 재전송"},
        404: {"description": "스캔 또는 원본 사진 없음"},
        409: {"description": "파일 내용 충돌 또는 제출 종료"},
        413: {"description": "설정한 파일 크기 제한 초과"},
        415: {"description": "지원하지 않는 요청 형식 또는 파일 형식"},
    },
)
def create_scan_file(
    scan_id: UUID,
    response: Response,
    kind: Annotated[FileKind, Form()],
    file: Annotated[UploadFile, File()],
    cam: Annotated[str | None, Form(min_length=1, max_length=50)] = None,
    angle_idx: Annotated[int | None, Form(ge=0, le=MAX_ANGLE_INDEX)] = None,
    sha256: Annotated[str | None, Form(pattern=r"^[0-9a-fA-F]{64}$")] = None,
):
    result = upload_scan_file(scan_id, kind, file.file, cam, angle_idx, sha256)
    if result["status"] == "duplicate":
        response.status_code = 200
    return result


@router.post(
    "/{scan_id}/submission",
    response_model=ScanSubmissionResponse,
    dependencies=[Depends(require_json)],
    summary="엣지 스캔 처리 결과 보고",
    responses={
        202: {"model": ScanSubmissionResponse, "description": "성공 제출 접수, 허브 분석 대기"},
        404: {"description": "등록되지 않은 스캔"},
        409: {"description": "제출 목록과 실제 업로드 파일 불일치"},
    },
)
def create_scan_submission(scan_id: UUID, payload: ScanSubmission, response: Response):
    result = submit_scan(scan_id, payload)
    if result["status"] == "processing" and not result["duplicate"]:
        response.status_code = 202
    return result
