from fastapi import APIRouter, Depends

from app.schemas.actuations import ActuationsRequest, ActuationsResponse
from app.security import require_json, verify_device_token
from app.services.actuations import save_actuations


router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["제어 결과"],
    dependencies=[Depends(verify_device_token), Depends(require_json)],
)


@router.post(
    "/actuations",
    response_model=ActuationsResponse,
    summary="제어 결과 업로드",
    responses={
        400: {"description": "잘못된 JSON 형식"},
        401: {"description": "장비 토큰 누락 또는 불일치"},
        404: {"description": "등록되지 않은 화분"},
        409: {"description": "같은 기록 ID의 내용 충돌"},
        415: {"description": "지원하지 않는 Content-Type"},
        422: {"description": "입력값 검증 오류"},
        500: {"description": "서버 처리 오류"},
        503: {"description": "일시적인 DB 접근 불가"},
    },
)
def upload_actuations(payload: ActuationsRequest):
    return save_actuations(payload)
