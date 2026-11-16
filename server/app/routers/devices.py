from fastapi import APIRouter, Depends

from app.schemas.devices import DeviceStatesRequest, DeviceStatesResponse
from app.security import require_json, verify_device_token
from app.services.devices import save_device_states


router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["장비"],
    dependencies=[Depends(verify_device_token), Depends(require_json)],
)


@router.post(
    "/device-states",
    response_model=DeviceStatesResponse,
    summary="장비 상태 보고",
    responses={
        400: {"description": "잘못된 JSON 형식"},
        401: {"description": "장비 토큰 누락 또는 불일치"},
        409: {"description": "장비 상태 또는 적용 설정 충돌"},
        415: {"description": "지원하지 않는 Content-Type"},
        422: {"description": "입력값 검증 오류"},
        500: {"description": "서버 처리 오류"},
        503: {"description": "일시적인 DB 접근 불가"},
    },
)
def upload_device_states(payload: DeviceStatesRequest):
    return save_device_states(payload)
