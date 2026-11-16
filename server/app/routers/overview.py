from typing import Annotated

from fastapi import APIRouter, Path

from app.schemas.overview import OverviewResponse
from app.services.overview import get_overview


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
