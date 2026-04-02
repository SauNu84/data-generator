import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, model_validator


# ─── Upload ──────────────────────────────────────────────────────────────────

_SDTYPE_TO_DETECTED: dict[str, str] = {
    "numerical": "numeric",
    "categorical": "categorical",
    "datetime": "datetime",
    "boolean": "boolean",
    "id": "categorical",
}


class ColumnSchema(BaseModel):
    name: str
    sdtype: str  # e.g. "numerical", "categorical", "datetime", "id"
    dtype: str   # pandas dtype string
    detected_type: str = ""  # mapped from sdtype: "numeric"|"categorical"|"datetime"|"boolean"

    @model_validator(mode="before")
    @classmethod
    def _compute_detected_type(cls, data: dict) -> dict:
        if isinstance(data, dict) and not data.get("detected_type"):
            sdtype = data.get("sdtype", "categorical")
            data["detected_type"] = _SDTYPE_TO_DETECTED.get(sdtype, "categorical")
        return data


class UploadResponse(BaseModel):
    dataset_id: uuid.UUID
    original_filename: str
    row_count: int
    columns: list[ColumnSchema]  # was `schema` — renamed to avoid BaseModel.schema shadowing


# ─── Generate ─────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_id: uuid.UUID
    # Accept both frontend name (row_count) and internal name (num_rows)
    num_rows: int = Field(
        default=100,
        ge=1,
        le=500_000,
        validation_alias=AliasChoices("num_rows", "row_count"),
    )
    # Accept both frontend name (model) and internal name (model_type)
    model_type: str = Field(
        default="GaussianCopula",
        pattern="^(GaussianCopula|CTGAN)$",
        validation_alias=AliasChoices("model_type", "model"),
    )
    schema_overrides: dict[str, str] | None = None


class GenerateResponse(BaseModel):
    job_id: uuid.UUID
    dataset_id: uuid.UUID
    status: str
    model_type: str


# ─── Job Status ───────────────────────────────────────────────────────────────

class ColumnQuality(BaseModel):
    column: str
    score: float  # 0–1


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    dataset_id: uuid.UUID
    status: str
    model_type: str
    requested_rows: int
    quality_score: float | None = None          # numeric 0–100 (was nested QualityScore object)
    column_quality: list[ColumnQuality] | None = None  # per-column detail
    error: str | None = None                    # was error_detail
    download_url: str | None = None             # presigned URL when status==done
    expires_at: datetime | None = None
    created_at: datetime
    completed_at: datetime | None = None


# ─── Download ─────────────────────────────────────────────────────────────────

class DownloadResponse(BaseModel):
    job_id: uuid.UUID
    url: str
    expires_in_seconds: int


# ─── Auth ─────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    tier: str
    is_email_verified: bool
    created_at: datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserProfile


# ─── API Keys ─────────────────────────────────────────────────────────────────

class ApiKeyCreateRequest(BaseModel):
    name: str = Field(default="Default", max_length=100)


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    request_count: int
    last_used_at: datetime | None
    revoked: bool
    created_at: datetime


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Only returned on creation — includes the full key."""
    key: str


# ─── Dashboard ────────────────────────────────────────────────────────────────

class DatasetSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    row_count: int
    created_at: datetime
    job_count: int = 0


class DashboardResponse(BaseModel):
    datasets: list[DatasetSummary]
    total: int
    page: int
    page_size: int


# ─── Billing ──────────────────────────────────────────────────────────────────

class CheckoutSessionResponse(BaseModel):
    checkout_url: str


class UsageSummaryResponse(BaseModel):
    tier: str
    monthly_generations_used: int
    monthly_generations_limit: int | None  # None = unlimited


# ─── Error ────────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    detail: str
