from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.schemas.commands import PositiveId, ReportTimestamp
from app.schemas.sensor import Number


FileKind = Literal["image", "mask", "cloud"]
ScanStatus = Literal["pending", "processing", "completed", "failed"]
MAX_ANGLE_INDEX = 2**31 - 1


class ScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScanRegistration(ScanInput):
    id: UUID
    plant_id: PositiveId
    ts: ReportTimestamp


class ScanRegistrationResponse(BaseModel):
    id: UUID
    plant_id: int
    day: int
    status: ScanStatus
    duplicate: bool


class ScanFileResponse(BaseModel):
    scan_id: UUID
    kind: FileKind
    cam: str | None
    angle_idx: int | None
    image_id: int | None
    status: Literal["created", "duplicate"]
    url: str
    w: int | None
    h: int | None


class SubmittedImage(ScanInput):
    cam: str = Field(min_length=1, max_length=50)
    angle_idx: int = Field(strict=True, ge=0, le=MAX_ANGLE_INDEX)
    mask_uploaded: StrictBool


class SuccessfulSubmission(ScanInput):
    outcome: Literal["success"]
    images: list[SubmittedImage]
    cloud_uploaded: StrictBool
    height_cm: Number | None = None
    canopy_cm: Number | None = None
    volume_cm3: Number | None = None


class SubmissionError(ScanInput):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)

    @field_validator("code", "message")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("공백만 입력할 수 없습니다.")
        return value


class FailedSubmission(ScanInput):
    outcome: Literal["failed"]
    error: SubmissionError


ScanSubmission = Annotated[
    SuccessfulSubmission | FailedSubmission,
    Field(discriminator="outcome"),
]


class ScanSubmissionResponse(BaseModel):
    scan_id: UUID
    status: ScanStatus
    duplicate: bool


class ScanImageDetail(BaseModel):
    id: int
    cam: str
    angle_idx: int
    url: str
    mask_url: str | None
    w: int
    h: int


class ScanSummary(BaseModel):
    id: UUID
    ts: AwareDatetime
    day: int
    status: ScanStatus
    height_cm: float | None
    canopy_cm: float | None
    volume_cm3: float | None
    leaf_pairs_est: float | None
    leaf_area_est: float | None
    leaf_pairs_manual: int | None


class ScanDetail(ScanSummary):
    plant_id: int
    cloud_url: str | None
    images: list[ScanImageDetail]


class ScanListItem(ScanSummary):
    image_count: int


class ScanListResponse(BaseModel):
    plant_id: int
    from_time: AwareDatetime = Field(alias="from")
    to: AwareDatetime
    items: list[ScanListItem]
    limit: int
    offset: int
    next_offset: int | None
