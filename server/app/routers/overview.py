from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query
from pydantic import BeforeValidator

from app.schemas.actuations import ActuationHistory, ActuationKind, ActuationSource
from app.schemas.commands import MAX_BIGINT, ReportTimestamp
from app.schemas.growth import GrowthResponse
from app.schemas.overview import OverviewResponse
from app.schemas.sensor_history import SensorHistoryResponse, SensorInterval
from app.services.actuations import get_actuation_history
from app.services.growth import get_growth
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


def parse_ok_query(value: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError("ok는 true 또는 false로 입력하세요.")
    return value == "true"


ActuationOkQuery = Annotated[bool, BeforeValidator(parse_ok_query)]


@router.get(
    "/{plant_id}/actuations",
    response_model=ActuationHistory,
    summary="제어 이력 조회",
    responses={
        404: {"description": "등록되지 않은 화분"},
        422: {"description": "화분 ID, 기간, 필터 또는 페이지 범위 오류"},
    },
)
def read_actuation_history(
    plant_id: Annotated[int, Path(ge=1, le=MAX_BIGINT)],
    from_time: Annotated[ReportTimestamp | None, Query(alias="from")] = None,
    to: Annotated[ReportTimestamp | None, Query()] = None,
    kind: Annotated[ActuationKind | None, Query()] = None,
    source: Annotated[ActuationSource | None, Query()] = None,
    ok: Annotated[ActuationOkQuery | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_BIGINT)] = 0,
):
    return get_actuation_history(
        plant_id, from_time, to, kind=kind, source=source, ok=ok, limit=limit, offset=offset,
    )


@router.get(
    "/{plant_id}/growth",
    response_model=GrowthResponse,
    summary="생장 예측 조회",
    responses={
        404: {"description": "등록되지 않은 화분, 모델 또는 스캔"},
        422: {"description": "요청 형식, 모델 종류 또는 스캔 소속 오류"},
    },
)
def read_growth(
    plant_id: Annotated[int, Path(ge=1, le=MAX_BIGINT)],
    model_id: Annotated[int | None, Query(ge=1, le=MAX_BIGINT)] = None,
    origin_scan_id: Annotated[UUID | None, Query()] = None,
):
    return get_growth(plant_id, model_id, origin_scan_id)
