import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.commands import PositiveId, ReportTimestamp


class EventItem(BaseModel):
    id: UUID
    plant_id: PositiveId
    ts: ReportTimestamp
    kind: Annotated[str, Field(min_length=1, max_length=50)]
    level: Literal["info", "warning", "error"]
    message: Annotated[str, Field(min_length=1)]
    data: dict[str, Any] | None = None

    @field_validator("kind", "message")
    @classmethod
    def check_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("공백만 입력할 수 없습니다.")
        if "\x00" in value:
            raise ValueError("문자열에 널 문자를 사용할 수 없습니다.")
        return value

    @field_validator("data")
    @classmethod
    def check_data(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value


class EventsRequest(BaseModel):
    items: list[EventItem] = Field(min_length=1, max_length=100)


class EventResult(BaseModel):
    index: int
    id: UUID
    status: Literal["created", "duplicate"]


class EventsResponse(BaseModel):
    results: list[EventResult]
