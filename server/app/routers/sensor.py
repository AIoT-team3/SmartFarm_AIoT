from fastapi import APIRouter, Depends

from app.schemas.sensor import (
    SensorReadingsRequest,
    SensorReadingsResponse,
)
from app.security import require_json, verify_device_token
from app.services.sensor import save_sensor_readings


router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["센서"],
    dependencies=[
        Depends(verify_device_token),
        Depends(require_json),
    ],
)


@router.post(
    "/sensor-readings",
    response_model=SensorReadingsResponse,
    status_code=200,
    summary="센서 기록 업로드",
    responses={
        400: {"description": "잘못된 JSON 형식"},
        401: {"description": "장비 인증 토큰 누락 또는 불일치"},
        404: {"description": "등록되지 않은 화분"},
        409: {"description": "동일 화분·측정 시각의 센서값 충돌"},
        415: {"description": "지원하지 않는 Content-Type"},
        422: {"description": "입력값 검증 오류"},
        500: {"description": "서버 처리 오류"},
        503: {"description": "일시적인 DB 접근 불가"},
    },
)
def upload_sensor_readings(payload: SensorReadingsRequest):
    return save_sensor_readings(payload)
