from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    field_validator,
)


# JSON 숫자만 허용하며 NaN·Infinity는 거절
Number = Annotated[
    float,
    Field(strict=True, allow_inf_nan=False),
]


class SensorReadingItem(BaseModel):
    """센서 측정 기록 한 건."""

    plant_id: int = Field(
        strict=True,
        ge=1,
        le=9223372036854775807,
    )
    ts: AwareDatetime

    air_temp_c: Number | None = None
    rh_pct: Number | None = Field(default=None, ge=0, le=100)
    soil_moisture_pct: Number | None = Field(
        default=None,
        ge=0,
        le=100,
    )
    lux: Number | None = Field(default=None, ge=0)
    ppfd: Number | None = Field(default=None, ge=0)
    vpd_kpa: Number | None = Field(default=None, ge=0)
    light_level: int | None = Field(
        default=None,
        strict=True,
        ge=-32768,
        le=32767,
    )

    @field_validator("ts", mode="before")
    @classmethod
    def parse_timestamp(cls, value):
        if not isinstance(value, str):
            raise ValueError(
                "ts는 시간대가 포함된 ISO 8601 문자열이어야 합니다."
            )
        return datetime.fromisoformat(value)


class SensorReadingsRequest(BaseModel):
    items: list[SensorReadingItem] = Field(
        min_length=1,
        max_length=100,
    )

class SensorReadingResult(BaseModel):
    index: int = Field(ge=0)
    id: int = Field(ge=1)
    status: Literal["created", "duplicate"]


class SensorReadingsResponse(BaseModel):
    results: list[SensorReadingResult]