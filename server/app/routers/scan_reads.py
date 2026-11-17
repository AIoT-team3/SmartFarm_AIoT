from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query
from fastapi.responses import FileResponse

from app.schemas.commands import MAX_BIGINT, ReportTimestamp
from app.schemas.scans import ScanDetail, ScanListResponse, ScanStatus
from app.services.scans import get_published_scan_file, get_scan_detail, list_scans


router = APIRouter()


@router.get(
    "/api/v1/plants/{plant_id}/scans",
    tags=["스캔"],
    response_model=ScanListResponse,
    summary="스캔 목록 조회",
    responses={404: {"description": "등록되지 않은 화분"}},
)
def read_scan_list(
    plant_id: Annotated[int, Path(ge=1, le=MAX_BIGINT)],
    from_time: Annotated[ReportTimestamp | None, Query(alias="from")] = None,
    to: Annotated[ReportTimestamp | None, Query()] = None,
    status: Annotated[ScanStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_BIGINT)] = 0,
):
    return list_scans(plant_id, from_time, to, status, limit, offset)


@router.get(
    "/api/v1/scans/{scan_id}",
    tags=["스캔"],
    response_model=ScanDetail,
    summary="스캔 상세 조회",
    responses={404: {"description": "스캔을 찾을 수 없음"}},
)
def read_scan(scan_id: UUID):
    return get_scan_detail(scan_id)


@router.get(
    "/files/{file_path:path}",
    tags=["파일"],
    response_class=FileResponse,
    summary="스캔 파일 제공",
    responses={
        200: {
            "description": "스캔 사진, 마스크 또는 점군 파일",
            "content": {
                media_type: {"schema": {"type": "string", "format": "binary"}}
                for media_type in ("image/jpeg", "image/png", "application/octet-stream")
            },
        },
        404: {"description": "없거나 공개 대상이 아닌 파일"},
        503: {"description": "일시적인 저장소 접근 불가"},
    },
)
def read_scan_file(file_path: str):
    path, media_type, file_stat = get_published_scan_file(file_path)
    return FileResponse(path, media_type=media_type, stat_result=file_stat)
