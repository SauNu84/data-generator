"""Tests for generation error states (launch gate: all error states covered)."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


@pytest.mark.anyio
async def test_get_failed_job_shows_error_detail(client, db_session):
    """A failed job must expose error_detail and null quality_score."""
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        original_filename="d.csv",
        s3_key="inputs/d.csv",
        row_count=5,
        schema_json=[],
    )
    db_session.add(dataset)
    await db_session.commit()

    job = GenerationJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        status="failed",
        model_type="GaussianCopula",
        requested_rows=100,
        error_detail="SDV fitting failed: singular covariance matrix",
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()

    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["quality_score"] is None
    assert "singular covariance" in body["error_detail"]


@pytest.mark.anyio
async def test_download_failed_job_returns_conflict(client, db_session):
    """Attempting to download a failed job must return a non-200 response."""
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        original_filename="d.csv",
        s3_key="inputs/d.csv",
        row_count=5,
        schema_json=[],
    )
    db_session.add(dataset)
    await db_session.commit()

    job = GenerationJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        status="failed",
        model_type="GaussianCopula",
        requested_rows=100,
        error_detail="Timed out",
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()

    resp = await client.get(f"/api/jobs/{job.id}/download")
    # Should be 409 (not done) since status is "failed"
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_generate_zero_rows_rejected(client, db_session):
    """Requesting zero synthetic rows must be rejected by Pydantic (422)."""
    dataset_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/generate",
        json={"dataset_id": dataset_id, "num_rows": 0, "model_type": "GaussianCopula"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_generate_too_many_rows_rejected(client, db_session):
    """Requesting more than 500k rows must be rejected by Pydantic (422)."""
    dataset_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/generate",
        json={"dataset_id": dataset_id, "num_rows": 500_001, "model_type": "GaussianCopula"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_job_running_has_no_quality_score(client, db_session):
    """A running job must not expose quality_score yet."""
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        original_filename="d.csv",
        s3_key="inputs/d.csv",
        row_count=5,
        schema_json=[],
    )
    db_session.add(dataset)
    await db_session.commit()

    job = GenerationJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        status="running",
        model_type="GaussianCopula",
        requested_rows=100,
    )
    db_session.add(job)
    await db_session.commit()

    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["quality_score"] is None
    assert resp.json()["status"] == "running"


@pytest.mark.anyio
async def test_shareable_url_accessible(client, db_session):
    """GET /api/jobs/{id} must work without any session or re-upload (shareable URL pattern)."""
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        original_filename="share.csv",
        s3_key="inputs/share.csv",
        row_count=10,
        schema_json=[],
    )
    db_session.add(dataset)
    await db_session.commit()

    job = GenerationJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        status="done",
        model_type="GaussianCopula",
        requested_rows=50,
        output_s3_key="outputs/share_result.csv",
        quality_score_json={"overall": 82.0, "columns": []},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()

    # Access the job URL directly — no auth, no prior upload in this request
    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["quality_score"]["overall"] == 82.0
