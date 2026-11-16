from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from pydantic import BeforeValidator

from app.schemas.commands import (
    MAX_BIGINT,
    ClaimRequest,
    ClaimResponse,
    CommandCreated,
    CommandDetails,
    CommandRequest,
    ResultReport,
    ResultResponse,
)
from app.security import require_json, verify_command_password, verify_device_token
from app.services.commands import (
    claim_commands,
    create_command,
    get_command,
    report_command_result,
)


router = APIRouter(prefix="/api/v1/commands", tags=["명령"])


def parse_command_id(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError("명령 ID는 양의 정수여야 합니다.")
    return int(value)


CommandId = Annotated[int, BeforeValidator(parse_command_id), Path(ge=1, le=MAX_BIGINT)]
dashboard_post = [Depends(verify_command_password), Depends(require_json)]
device_post = [Depends(verify_device_token), Depends(require_json)]


@router.post(
    "",
    response_model=CommandCreated,
    status_code=201,
    summary="명령 요청",
    dependencies=dashboard_post,
    responses={
        200: {"model": CommandCreated, "description": "동일 명령 요청 재전송"},
        401: {"description": "명령 비밀번호 인증 실패"},
        404: {"description": "등록되지 않은 화분"},
        409: {"description": "같은 요청 ID에 다른 내용"},
    },
)
def request_command(payload: CommandRequest, response: Response):
    command = create_command(payload)
    if command["duplicate"]:
        response.status_code = 200
    return command


@router.post(
    "/claim",
    response_model=ClaimResponse,
    summary="처리할 명령 가져오기",
    dependencies=device_post,
    responses={
        401: {"description": "장비 토큰 인증 실패"},
        404: {"description": "등록되지 않은 화분"},
    },
)
def take_commands(payload: ClaimRequest):
    return {"commands": claim_commands(payload)}


@router.get(
    "/{command_id}",
    response_model=CommandDetails,
    summary="명령 상태 조회",
    responses={404: {"description": "명령을 찾을 수 없음"}},
)
def read_command(command_id: CommandId):
    return get_command(command_id)


@router.post(
    "/{command_id}/result",
    response_model=ResultResponse,
    summary="명령 처리 결과 보고",
    dependencies=device_post,
    responses={
        401: {"description": "장비 토큰 인증 실패"},
        404: {"description": "명령 또는 등록 장비를 찾을 수 없음"},
        409: {"description": "명령 상태, 종료 결과 또는 설정 버전 충돌"},
    },
)
def record_result(command_id: CommandId, payload: ResultReport):
    return report_command_result(command_id, payload)
