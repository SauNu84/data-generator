"""Tests for GET /api/jobs/{job_id} and GET /api/jobs/{job_id}/download."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


@pytest.mark.anyio
async def test_get_job_queued(auth_client, db_session, test_user):
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
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

    resp = await auth_client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["quality_score"] is None


@pytest.mark.anyio
async def test_get_job_done_with_quality(auth_client, db_session, test_user):
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
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

    fake_url = "http://minio:9000/datagen-files/outputs/result.csv?sig=abc"
    with patch("app.main.generate_presigned_url", return_value=fake_url):
        resp = await auth_client.get(f"/api/jobs/{job.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    # quality_score must be a flat number, not an object
    assert isinstance(body["quality_score"], float)
    assert body["quality_score"] == 84.5
    # column_quality carries per-column detail
    assert body["column_quality"] is not None
    assert len(body["column_quality"]) == 1
    assert body["column_quality"][0]["column"] == "age"
    # download_url must be inline in job status
    assert body["download_url"] == fake_url


@pytest.mark.anyio
async def test_get_job_not_found(auth_client):
    resp = await auth_client.get(f"/api/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_download_done_job(auth_client, db_session, test_user):
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
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
        resp = await auth_client.get(f"/api/jobs/{job.id}/download")

    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == fake_url
    assert body["expires_in_seconds"] == 86400


@pytest.mark.anyio
async def test_download_not_done(auth_client, db_session, test_user):
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
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

    resp = await auth_client.get(f"/api/jobs/{job.id}/download")
    assert resp.status_code == 409
    assert "not done" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_download_expired(auth_client, db_session, test_user):
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
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

    resp = await auth_client.get(f"/api/jobs/{job.id}/download")
    assert resp.status_code == 410


# ─── Contract regression tests ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_job_status_error_field_not_error_detail(auth_client, db_session, test_user):
    """Contract: field must be `error`, not `error_detail`."""
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
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
        error_detail="SDV fitting timed out",
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()

    resp = await auth_client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] == "SDV fitting timed out"
    assert "error_detail" not in body


@pytest.mark.anyio
async def test_job_status_quality_score_is_float(auth_client, db_session, test_user):
    """Contract: quality_score must be a number (0-100), not an object."""
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
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
        quality_score_json={"overall": 92.1, "columns": []},
        completed_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(job)
    await db_session.commit()

    fake_url = "http://minio:9000/out.csv?sig=x"
    with patch("app.main.generate_presigned_url", return_value=fake_url):
        resp = await auth_client.get(f"/api/jobs/{job.id}")

    body = resp.json()
    assert isinstance(body["quality_score"], float)
    assert body["quality_score"] == 92.1


@pytest.mark.anyio
async def test_job_status_download_url_present_for_done_job(auth_client, db_session, test_user):
    """Contract: download_url must be inline in job status for completed jobs."""
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
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
        completed_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(job)
    await db_session.commit()

    fake_url = "http://minio:9000/datagen-files/outputs/result.csv?presigned=token"
    with patch("app.main.generate_presigned_url", return_value=fake_url):
        resp = await auth_client.get(f"/api/jobs/{job.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["download_url"] == fake_url


@pytest.mark.anyio
async def test_job_status_download_url_none_for_non_done_job(auth_client, db_session, test_user):
    """Contract: download_url must be None when job is not done."""
    from app.models import Dataset, GenerationJob

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
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

    resp = await auth_client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["download_url"] is None
