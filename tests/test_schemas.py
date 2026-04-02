"""Unit tests for Pydantic schema validation (no HTTP, no DB)."""

import uuid
from datetime import datetime, timezone

import pytest

from app.schemas import (
    ColumnQuality,
    ColumnSchema,
    DownloadResponse,
    GenerateRequest,
    GenerateResponse,
    JobStatusResponse,
    UploadResponse,
)


# ─── ColumnSchema ─────────────────────────────────────────────────────────────

def test_column_schema_valid():
    c = ColumnSchema(name="age", sdtype="numerical", dtype="int64")
    assert c.name == "age"
    assert c.sdtype == "numerical"


def test_column_schema_detected_type_numerical():
    c = ColumnSchema(name="age", sdtype="numerical", dtype="int64")
    assert c.detected_type == "numeric"


def test_column_schema_detected_type_categorical():
    c = ColumnSchema(name="city", sdtype="categorical", dtype="object")
    assert c.detected_type == "categorical"


def test_column_schema_detected_type_datetime():
    c = ColumnSchema(name="ts", sdtype="datetime", dtype="datetime64[ns]")
    assert c.detected_type == "datetime"


def test_column_schema_detected_type_boolean():
    c = ColumnSchema(name="flag", sdtype="boolean", dtype="bool")
    assert c.detected_type == "boolean"


def test_column_schema_detected_type_id_maps_to_categorical():
    c = ColumnSchema(name="user_id", sdtype="id", dtype="object")
    assert c.detected_type == "categorical"


def test_column_schema_detected_type_unknown_maps_to_categorical():
    c = ColumnSchema(name="x", sdtype="unknown_type", dtype="object")
    assert c.detected_type == "categorical"


# ─── UploadResponse ───────────────────────────────────────────────────────────

def test_upload_response_round_trip():
    col = ColumnSchema(name="salary", sdtype="numerical", dtype="float64")
    r = UploadResponse(
        dataset_id=uuid.uuid4(),
        original_filename="data.csv",
        row_count=1000,
        columns=[col],
    )
    dumped = r.model_dump()
    assert dumped["original_filename"] == "data.csv"
    assert dumped["row_count"] == 1000
    assert len(dumped["columns"]) == 1


def test_upload_response_no_schema_field_name_conflict():
    """UploadResponse.columns must not shadow BaseModel.schema class method."""
    r = UploadResponse(
        dataset_id=uuid.uuid4(),
        original_filename="data.csv",
        row_count=10,
        columns=[],
    )
    assert callable(r.model_json_schema)  # BaseModel class method intact
    assert hasattr(r, "columns")


# ─── GenerateRequest ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("model_type", ["GaussianCopula", "CTGAN"])
def test_generate_request_valid_model_types(model_type):
    req = GenerateRequest(dataset_id=uuid.uuid4(), num_rows=100, model_type=model_type)
    assert req.model_type == model_type


def test_generate_request_invalid_model_type():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GenerateRequest(dataset_id=uuid.uuid4(), num_rows=100, model_type="InvalidModel")


def test_generate_request_row_count_lower_bound():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GenerateRequest(dataset_id=uuid.uuid4(), num_rows=0)


def test_generate_request_row_count_upper_bound():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GenerateRequest(dataset_id=uuid.uuid4(), num_rows=500_001)


def test_generate_request_row_count_at_limit():
    req = GenerateRequest(dataset_id=uuid.uuid4(), num_rows=500_000)
    assert req.num_rows == 500_000


def test_generate_request_defaults():
    req = GenerateRequest(dataset_id=uuid.uuid4())
    assert req.num_rows == 100
    assert req.model_type == "GaussianCopula"


# Contract regression: frontend sends row_count / model
def test_generate_request_accepts_frontend_field_names():
    """Frontend sends row_count + model — must map to num_rows + model_type."""
    req = GenerateRequest.model_validate(
        {"dataset_id": str(uuid.uuid4()), "row_count": 250, "model": "CTGAN"}
    )
    assert req.num_rows == 250
    assert req.model_type == "CTGAN"


def test_generate_request_accepts_backend_field_names():
    """Backend field names must still work (populate_by_name=True)."""
    req = GenerateRequest.model_validate(
        {"dataset_id": str(uuid.uuid4()), "num_rows": 50, "model_type": "GaussianCopula"}
    )
    assert req.num_rows == 50
    assert req.model_type == "GaussianCopula"


def test_generate_request_schema_overrides_optional():
    req = GenerateRequest(dataset_id=uuid.uuid4())
    assert req.schema_overrides is None


def test_generate_request_schema_overrides_accepted():
    req = GenerateRequest.model_validate(
        {
            "dataset_id": str(uuid.uuid4()),
            "row_count": 100,
            "model": "GaussianCopula",
            "schema_overrides": {"age": "numeric", "city": "categorical"},
        }
    )
    assert req.schema_overrides == {"age": "numeric", "city": "categorical"}


# ─── GenerateResponse ─────────────────────────────────────────────────────────

def test_generate_response_valid():
    r = GenerateResponse(
        job_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status="queued",
        model_type="GaussianCopula",
    )
    assert r.status == "queued"


# ─── JobStatusResponse ────────────────────────────────────────────────────────

def test_job_status_quality_score_is_numeric():
    """quality_score must be a float, not a nested object."""
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(
        job_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status="done",
        model_type="GaussianCopula",
        requested_rows=100,
        quality_score=78.3,
        column_quality=[ColumnQuality(column="salary", score=0.78)],
        created_at=now,
        completed_at=now,
    )
    assert isinstance(r.quality_score, float)
    assert r.quality_score == 78.3
    assert r.column_quality is not None
    assert r.column_quality[0].column == "salary"


def test_job_status_without_quality_score():
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(
        job_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status="queued",
        model_type="GaussianCopula",
        requested_rows=100,
        created_at=now,
    )
    assert r.quality_score is None
    assert r.error is None


def test_job_status_error_field_name():
    """Field must be named `error`, not `error_detail`."""
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(
        job_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status="failed",
        model_type="GaussianCopula",
        requested_rows=100,
        error="SDV fitting timed out",
        created_at=now,
        completed_at=now,
    )
    assert r.error == "SDV fitting timed out"
    assert r.quality_score is None
    dumped = r.model_dump()
    assert "error" in dumped
    assert "error_detail" not in dumped


def test_job_status_download_url_field():
    """download_url must appear in job status response."""
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(
        job_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status="done",
        model_type="GaussianCopula",
        requested_rows=100,
        download_url="http://minio:9000/bucket/outputs/file.csv?sig=abc",
        created_at=now,
        completed_at=now,
    )
    assert r.download_url is not None
    assert "minio" in r.download_url


def test_job_status_download_url_none_when_not_done():
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(
        job_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status="running",
        model_type="GaussianCopula",
        requested_rows=100,
        created_at=now,
    )
    assert r.download_url is None


# ─── DownloadResponse ─────────────────────────────────────────────────────────

def test_download_response_valid():
    r = DownloadResponse(
        job_id=uuid.uuid4(),
        url="http://minio:9000/bucket/key?presigned=token",
        expires_in_seconds=86400,
    )
    assert r.expires_in_seconds == 86400
    assert "presigned" in r.url
