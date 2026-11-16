from typing import Any

from pydantic import AwareDatetime, BaseModel, Field


class PlantOverview(BaseModel):
    id: int
    name: str
    species: str
    sown_at: AwareDatetime


class LatestSensor(BaseModel):
    id: int
    ts: AwareDatetime
    age_seconds: int
    air_temp_c: float | None
    rh_pct: float | None
    soil_moisture_pct: float | None
    lux: float | None
    ppfd: float | None
    vpd_kpa: float | None
    light_level: int | None


class DeviceOverview(BaseModel):
    name: str
    role: str
    online: bool | None
    ts: AwareDatetime | None
    age_seconds: int | None


class AppliedSettings(BaseModel):
    device_name: str
    applied_settings: dict[str, Any]
    settings_revision: int = Field(ge=0)
    settings_applied_at: AwareDatetime


class OverviewResponse(BaseModel):
    as_of: AwareDatetime
    plant: PlantOverview
    latest_sensor: LatestSensor | None
    devices: list[DeviceOverview]
    settings: AppliedSettings | None
