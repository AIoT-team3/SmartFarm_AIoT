from fastapi import APIRouter, Depends

from app.schemas.events import EventsRequest, EventsResponse
from app.security import require_json, verify_device_token
from app.services.events import save_events


router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["경고·이벤트"],
    dependencies=[Depends(verify_device_token), Depends(require_json)],
)


@router.post(
    "/events",
    response_model=EventsResponse,
    summary="경고·이벤트 업로드",
    responses={
        400: {"description": "잘못된 JSON 형식"},
        401: {"description": "장비 토큰 누락 또는 불일치"},
        404: {"description": "등록되지 않은 화분"},
        409: {"description": "같은 사건 ID의 내용 충돌"},
        415: {"description": "지원하지 않는 Content-Type"},
        422: {"description": "입력값 검증 오류"},
        500: {"description": "서버 처리 오류"},
        503: {"description": "일시적인 DB 접근 불가"},
    },
)
def upload_events(payload: EventsRequest):
    return save_events(payload)
