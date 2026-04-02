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
    QualityScore,
    UploadResponse,
)


# ─── ColumnSchema ─────────────────────────────────────────────────────────────

def test_column_schema_valid():
    c = ColumnSchema(name="age", sdtype="numerical", dtype="int64")
    assert c.name == "age"
    assert c.sdtype == "numerical"


# ─── UploadResponse ───────────────────────────────────────────────────────────

def test_upload_response_round_trip():
    col = ColumnSchema(name="salary", sdtype="numerical", dtype="float64")
    r = UploadResponse(
        dataset_id=uuid.uuid4(),
        original_filename="data.csv",
        row_count=1000,
        schema=[col],
    )
    dumped = r.model_dump()
    assert dumped["original_filename"] == "data.csv"
    assert dumped["row_count"] == 1000
    assert len(dumped["schema"]) == 1


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


# ─── GenerateResponse ─────────────────────────────────────────────────────────

def test_generate_response_valid():
    r = GenerateResponse(
        job_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status="queued",
        model_type="GaussianCopula",
    )
    assert r.status == "queued"


# ─── QualityScore ─────────────────────────────────────────────────────────────

def test_quality_score_overall_range():
    q = QualityScore(
        overall=84.5,
        columns=[ColumnQuality(column="age", score=0.85)],
    )
    assert 0 <= q.overall <= 100
    assert 0 <= q.columns[0].score <= 1


def test_quality_score_zero():
    q = QualityScore(overall=0.0, columns=[])
    assert q.overall == 0.0
    assert q.columns == []


# ─── JobStatusResponse ────────────────────────────────────────────────────────

def test_job_status_with_quality_score():
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(
        job_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status="done",
        model_type="GaussianCopula",
        requested_rows=100,
        quality_score=QualityScore(
            overall=78.3,
            columns=[ColumnQuality(column="salary", score=0.78)],
        ),
        created_at=now,
        completed_at=now,
    )
    assert r.quality_score is not None
    assert r.quality_score.overall == 78.3


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
    assert r.error_detail is None


def test_job_status_failed_with_error():
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(
        job_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        status="failed",
        model_type="GaussianCopula",
        requested_rows=100,
        error_detail="SDV fitting timed out",
        created_at=now,
        completed_at=now,
    )
    assert r.error_detail == "SDV fitting timed out"
    assert r.quality_score is None


# ─── DownloadResponse ─────────────────────────────────────────────────────────

def test_download_response_valid():
    r = DownloadResponse(
        job_id=uuid.uuid4(),
        url="http://minio:9000/bucket/key?presigned=token",
        expires_in_seconds=86400,
    )
    assert r.expires_in_seconds == 86400
    assert "presigned" in r.url
