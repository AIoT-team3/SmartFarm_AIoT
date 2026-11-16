import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.errors import APIError
from app.schemas.sensor import Number


MAX_BIGINT = 2**63 - 1
PositiveId = Annotated[int, Field(strict=True, ge=1, le=MAX_BIGINT)]
CommandKind = Literal["water", "scan_now", "set_watering", "estop"]
CommandStatus = Literal["pending", "picked", "done", "failed"]
TerminalStatus = Literal["done", "failed"]
SettingsStatus = Literal["updated", "duplicate", "ignored_stale", "not_provided"]
WateringMode = Literal["predictive", "threshold", "off"]
Percentage = Annotated[Number, Field(ge=0, le=100)]
Nonblank = Annotated[str, Field(min_length=1)]


def parse_report_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("시간대가 포함된 ISO 8601 문자열이어야 합니다.")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("시각에 시간대가 필요합니다.")
    return parsed.astimezone(timezone.utc)


ReportTimestamp = Annotated[AwareDatetime, BeforeValidator(parse_report_timestamp)]


class CommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WaterPayload(CommandInput):
    amount_ml: Number = Field(gt=0)


class EstopPayload(CommandInput):
    enabled: bool = Field(strict=True)


class WateringPayload(CommandInput):
    mode: WateringMode
    lower_pct: Percentage | None = None
    target_pct: Percentage | None = None

    @model_validator(mode="after")
    def check_thresholds(self):
        if self.mode == "off":
            if self.lower_pct is not None or self.target_pct is not None:
                raise ValueError("off 요청은 기존 임계값을 유지합니다.")
        elif (
            self.lower_pct is None
            or self.target_pct is None
            or self.lower_pct >= self.target_pct
        ):
            raise ValueError("하한과 목표를 입력하고 하한을 목표보다 낮게 설정하세요.")
        return self


class CommandRequestBase(CommandInput):
    request_id: UUID
    plant_id: PositiveId


class WaterRequest(CommandRequestBase):
    kind: Literal["water"]
    payload: WaterPayload


class ScanRequest(CommandRequestBase):
    kind: Literal["scan_now"]
    payload: None = None


class WateringRequest(CommandRequestBase):
    kind: Literal["set_watering"]
    payload: WateringPayload


class EstopRequest(CommandRequestBase):
    kind: Literal["estop"]
    payload: EstopPayload


CommandRequest = Annotated[
    WaterRequest | ScanRequest | WateringRequest | EstopRequest,
    Field(discriminator="kind"),
]


class ClaimRequest(CommandInput):
    plant_id: PositiveId
    limit: int = Field(default=20, strict=True, ge=1, le=100)


class AppliedWateringSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    watering_mode: WateringMode
    soil_lower_pct: Percentage
    soil_target_pct: Percentage

    @model_validator(mode="after")
    def check_settings(self):
        if self.soil_lower_pct >= self.soil_target_pct:
            raise ValueError("급수 하한은 목표보다 낮아야 합니다.")
        json.dumps(self.model_dump(), allow_nan=False)
        return self


class CommonResult(CommandInput):
    finished_at: ReportTimestamp
    message: Nonblank
    code: Nonblank | None = None

    @field_validator("message", "code")
    @classmethod
    def check_nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("공백만 입력할 수 없습니다.")
        return value


class WaterResult(CommonResult):
    amount_ml: Number | None = Field(default=None, ge=0)
    actuation_id: UUID | None = None


class ScanResult(CommonResult):
    scan_id: UUID


class WateringResult(CommonResult):
    device_name: str = Field(min_length=1, max_length=50)
    applied_settings: AppliedWateringSettings
    settings_revision: int = Field(strict=True, ge=0)
    settings_applied_at: ReportTimestamp


class EstopResult(CommonResult):
    enabled: bool = Field(strict=True)


class ResultReport(CommandInput):
    status: TerminalStatus
    result: dict

    def validated_result(self, kind: CommandKind) -> dict:
        if kind == "water":
            schema = WaterResult
        elif self.status == "done":
            schema = {
                "scan_now": ScanResult,
                "set_watering": WateringResult,
                "estop": EstopResult,
            }[kind]
        else:
            schema = CommonResult

        try:
            result = schema.model_validate(self.result)
        except ValidationError as exc:
            raise APIError(
                422,
                "VALIDATION_ERROR",
                "명령 결과의 필수값, 타입 또는 범위를 확인하세요.",
                [
                    {"field": ".".join(("result", *map(str, error["loc"])))}
                    for error in exc.errors()
                ],
            ) from exc

        if self.status == "failed" and result.code is None:
            raise APIError(
                422, "VALIDATION_ERROR", "실패 보고에는 사유 코드가 필요합니다.",
                [{"field": "result.code"}],
            )

        return result.model_dump(mode="json", exclude_none=True)


class CommandDetails(BaseModel):
    id: int
    request_id: UUID
    plant_id: int
    kind: CommandKind
    payload: dict | None
    status: CommandStatus
    result: dict | None
    created_at: AwareDatetime
    picked_at: AwareDatetime | None
    done_at: AwareDatetime | None


class CommandCreated(CommandDetails):
    duplicate: bool


class ClaimedCommand(BaseModel):
    id: int
    request_id: UUID
    plant_id: int
    kind: CommandKind
    payload: dict | None
    status: Literal["picked"]
    created_at: AwareDatetime
    picked_at: AwareDatetime
    expires_at: AwareDatetime | None


class ClaimResponse(BaseModel):
    commands: list[ClaimedCommand]


class ResultResponse(BaseModel):
    id: int
    status: TerminalStatus
    done_at: AwareDatetime
    duplicate: bool
    settings_status: SettingsStatus
