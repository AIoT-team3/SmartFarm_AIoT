from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.schemas.commands import MAX_BIGINT, ReportTimestamp
from app.schemas.overview import OverviewResponse
from app.schemas.sensor_history import SensorHistoryResponse, SensorInterval
from app.services.overview import get_overview
from app.services.sensor_history import get_sensor_history


router = APIRouter(prefix="/api/v1/plants", tags=["대시보드"])


@router.get(
    "/{plant_id}/overview",
    response_model=OverviewResponse,
    summary="대시보드 현재 상태 조회",
    responses={
        404: {"description": "등록되지 않은 화분"},
        422: {"description": "화분 ID 검증 오류"},
        500: {"description": "서버 처리 오류"},
        503: {"description": "일시적인 DB 접근 불가"},
    },
)
def read_overview(
    plant_id: Annotated[int, Path(ge=1, le=9223372036854775807)],
):
    return get_overview(plant_id)


@router.get(
    "/{plant_id}/sensor-readings",
    response_model=SensorHistoryResponse,
    summary="센서 이력 조회",
    responses={
        404: {"description": "등록되지 않은 화분"},
        422: {"description": "화분 ID, 기간, 간격 또는 페이지 범위 오류"},
    },
)
def read_sensor_history(
    plant_id: Annotated[int, Path(ge=1, le=MAX_BIGINT)],
    from_time: Annotated[ReportTimestamp | None, Query(alias="from")] = None,
    to: Annotated[ReportTimestamp | None, Query()] = None,
    interval: Annotated[SensorInterval, Query()] = "10m",
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_BIGINT)] = 0,
):
    return get_sensor_history(plant_id, from_time, to, interval, limit, offset)
