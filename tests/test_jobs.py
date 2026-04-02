"""Tests for GET /api/jobs/{job_id} and GET /api/jobs/{job_id}/download."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


@pytest.mark.anyio
async def test_get_job_queued(client, db_session):
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
        status="queued",
        model_type="GaussianCopula",
        requested_rows=100,
    )
    db_session.add(job)
    await db_session.commit()

    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["quality_score"] is None


@pytest.mark.anyio
async def test_get_job_done_with_quality(client, db_session):
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
        status="done",
        model_type="GaussianCopula",
        requested_rows=100,
        output_s3_key="outputs/result.csv",
        quality_score_json={
            "overall": 84.5,
            "columns": [{"column": "age", "score": 0.85}],
        },
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()

    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["quality_score"]["overall"] == 84.5
    assert len(body["quality_score"]["columns"]) == 1


@pytest.mark.anyio
async def test_get_job_not_found(client):
    resp = await client.get(f"/api/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_download_done_job(client, db_session):
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
        status="done",
        model_type="GaussianCopula",
        requested_rows=50,
        output_s3_key="outputs/result.csv",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()

    fake_url = "http://minio:9000/datagen-files/outputs/result.csv?presigned=token"
    with patch("app.main.generate_presigned_url", return_value=fake_url):
        resp = await client.get(f"/api/jobs/{job.id}/download")

    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == fake_url
    assert body["expires_in_seconds"] == 86400


@pytest.mark.anyio
async def test_download_not_done(client, db_session):
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
        requested_rows=50,
    )
    db_session.add(job)
    await db_session.commit()

    resp = await client.get(f"/api/jobs/{job.id}/download")
    assert resp.status_code == 409
    assert "not done" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_download_expired(client, db_session):
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
        status="done",
        model_type="GaussianCopula",
        requested_rows=50,
        output_s3_key=None,  # already cleaned up
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        completed_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )
    db_session.add(job)
    await db_session.commit()

    resp = await client.get(f"/api/jobs/{job.id}/download")
    assert resp.status_code == 410
