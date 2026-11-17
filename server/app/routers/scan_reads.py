from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.schemas.scans import ScanDetail
from app.services.scans import get_published_scan_file, get_scan_detail


router = APIRouter()


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
