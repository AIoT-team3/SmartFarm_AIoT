import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.schemas.commands import Percentage, PositiveId, ReportTimestamp
from app.schemas.sensor import Number


class ActuationContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    measured_soil_pct: Percentage | None = None
    predicted_soil_pct: Percentage | None = None
    forecast_hours: Number | None = Field(default=None, gt=0)
    threshold_pct: Percentage | None = None
    target_pct: Percentage | None = None
    control_mode: str | None = None

    @model_validator(mode="after")
    def validate_context(self):
        for field in self.model_fields_set.intersection(type(self).model_fields):
            if getattr(self, field) is None:
                raise ValueError(f"값을 알 수 없는 {field} 필드는 생략하세요.")
        json.dumps(self.model_extra, allow_nan=False)
        return self


class ActuationItem(BaseModel):
    id: UUID
    plant_id: PositiveId
    ts: ReportTimestamp
    kind: Literal["water", "light"]
    amount: Number | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    ok: StrictBool
    reason: str | None = None
    source: Literal["auto", "manual", "guard"]
    context: ActuationContext | None = None

    @model_validator(mode="after")
    def validate_unit(self):
        if self.amount is not None and self.unit is None:
            raise ValueError("수행량이 있으면 단위도 보내야 합니다.")
        if self.unit is not None:
            if not self.unit.strip():
                raise ValueError("단위에 공백만 입력할 수 없습니다.")
            if self.kind == "water" and self.unit != "mL":
                raise ValueError("급수 단위는 mL로 보내야 합니다.")
        return self


class ActuationsRequest(BaseModel):
    items: list[ActuationItem] = Field(min_length=1, max_length=100)


class ActuationResult(BaseModel):
    index: int
    id: UUID
    status: Literal["created", "duplicate"]


class ActuationsResponse(BaseModel):
    results: list[ActuationResult]
