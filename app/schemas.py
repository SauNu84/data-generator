import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ─── Upload ──────────────────────────────────────────────────────────────────

class ColumnSchema(BaseModel):
    name: str
    sdtype: str  # e.g. "numerical", "categorical", "datetime", "id"
    dtype: str   # pandas dtype string


class UploadResponse(BaseModel):
    dataset_id: uuid.UUID
    original_filename: str
    row_count: int
    schema: list[ColumnSchema]


# ─── Generate ─────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    dataset_id: uuid.UUID
    num_rows: int = Field(default=100, ge=1, le=500_000)
    model_type: str = Field(default="GaussianCopula", pattern="^(GaussianCopula|CTGAN)$")


class GenerateResponse(BaseModel):
    job_id: uuid.UUID
    dataset_id: uuid.UUID
    status: str
    model_type: str


# ─── Job Status ───────────────────────────────────────────────────────────────

class ColumnQuality(BaseModel):
    column: str
    score: float  # 0–1


class QualityScore(BaseModel):
    overall: float  # 0–100 percent
    columns: list[ColumnQuality]


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    dataset_id: uuid.UUID
    status: str
    model_type: str
    requested_rows: int
    quality_score: QualityScore | None = None
    error_detail: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    completed_at: datetime | None = None


# ─── Download ─────────────────────────────────────────────────────────────────

class DownloadResponse(BaseModel):
    job_id: uuid.UUID
    url: str
    expires_in_seconds: int


# ─── Error ────────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    detail: str
