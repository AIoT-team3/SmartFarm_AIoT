import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.schemas.commands import AppliedWateringSettings, ReportTimestamp, SettingsStatus


SETTINGS_KEYS = ("applied_settings", "settings_revision", "settings_applied_at")
StateStatus = Literal["created", "updated", "duplicate", "ignored_stale"]


class DeviceStateInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    applied_settings: AppliedWateringSettings | None = None
    settings_revision: int | None = Field(default=None, strict=True, ge=0)
    settings_applied_at: ReportTimestamp | None = None

    @model_validator(mode="after")
    def validate_info(self):
        sent = self.model_fields_set.intersection(SETTINGS_KEYS)
        if sent and any(getattr(self, key) is None for key in SETTINGS_KEYS):
            raise ValueError("적용 설정, 버전, 적용 시각은 모두 보내거나 모두 생략하세요.")
        json.dumps(self.model_extra, allow_nan=False)
        return self


class DeviceStateItem(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    role: str = Field(min_length=1, max_length=30)
    online: StrictBool | None
    ts: ReportTimestamp
    info: DeviceStateInfo = Field(default_factory=DeviceStateInfo)


class DeviceStatesRequest(BaseModel):
    items: list[DeviceStateItem] = Field(min_length=1, max_length=100)


class DeviceStateResult(BaseModel):
    index: int
    name: str
    state_status: StateStatus
    settings_status: SettingsStatus


class DeviceStatesResponse(BaseModel):
    results: list[DeviceStateResult]
