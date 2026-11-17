from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.schemas.sensor import SensorValues


SensorInterval = Literal["10m", "raw"]


class RawSensorReading(SensorValues):
    id: int
    ts: AwareDatetime


class SensorValidCounts(BaseModel):
    air_temp_c: int
    rh_pct: int
    soil_moisture_pct: int
    lux: int
    ppfd: int
    vpd_kpa: int
    light_level: int


class TenMinuteSensorReading(SensorValues):
    ts: AwareDatetime
    sample_count: int
    valid_counts: SensorValidCounts


class SensorHistoryPage(BaseModel):
    plant_id: int
    from_time: AwareDatetime = Field(alias="from")
    to: AwareDatetime
    limit: int
    offset: int
    next_offset: int | None


class RawSensorHistory(SensorHistoryPage):
    interval: Literal["raw"]
    items: list[RawSensorReading]


class TenMinuteSensorHistory(SensorHistoryPage):
    interval: Literal["10m"]
    items: list[TenMinuteSensorReading]


SensorHistoryResponse = Annotated[
    RawSensorHistory | TenMinuteSensorHistory,
    Field(discriminator="interval"),
]
