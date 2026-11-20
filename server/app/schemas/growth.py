from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel


class GrowthModel(BaseModel):
    id: int
    kind: Literal["growth"]
    status: Literal["candidate", "active", "archived"]


class GrowthOriginScan(BaseModel):
    id: UUID
    ts: AwareDatetime
    day: int


class BacktestResult(BaseModel):
    prediction_id: int
    horizon: int
    predicted: float
    observed: float
    abs_err: float
    in_band: bool | None


class GrowthPrediction(BaseModel):
    id: int
    origin_day: int
    target_day: int
    target_date: date
    height_mu: float
    p10: float | None
    p90: float | None
    pairs_mu: float | None
    backtest: BacktestResult | None


class BacktestSummary(BaseModel):
    evaluated_count: int
    mae_cm: float | None
    band_evaluated_count: int
    band_hit_rate: float | None


class GrowthState(BaseModel):
    model_id: int
    regime_start: AwareDatetime | None
    anomaly_streak: int
    updated_at: AwareDatetime


class HarvestEstimate(BaseModel):
    status: Literal["available", "not_available", "not_reached"]
    estimated_date: date | None
    remaining_days: int | None


class GrowthResponse(BaseModel):
    plant_id: int
    status: Literal["available", "no_model", "no_prediction"]
    model: GrowthModel | None
    origin_scan: GrowthOriginScan | None
    predictions: list[GrowthPrediction]
    backtest_summary: BacktestSummary
    growth_state: GrowthState | None
    harvest: HarvestEstimate
