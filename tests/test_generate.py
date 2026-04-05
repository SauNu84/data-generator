"""Tests for POST /api/generate."""

import uuid
from unittest.mock import patch

import pytest


@pytest.mark.anyio
async def test_generate_happy_path(auth_client, db_session, test_user, sample_csv_bytes):
    from app.models import Dataset

    # Insert a dataset first
    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        original_filename="train.csv",
        s3_key="inputs/abc.csv",
        row_count=10,
        schema_json=[{"name": "age", "sdtype": "numerical", "dtype": "int64"}],
    )
    db_session.add(dataset)
    await db_session.commit()

    with patch("app.main.generate_synthetic_data") as mock_task:
        mock_task.delay = lambda *a, **kw: None

        resp = await auth_client.post(
            "/api/generate",
            json={
                "dataset_id": str(dataset.id),
                "num_rows": 50,
                "model_type": "GaussianCopula",
            },
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["model_type"] == "GaussianCopula"
    assert "job_id" in body


@pytest.mark.anyio
async def test_generate_frontend_field_names(auth_client, db_session, test_user):
    """Contract regression: frontend sends row_count + model — must return 202, not 422."""
    from app.models import Dataset

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        original_filename="train.csv",
        s3_key="inputs/abc.csv",
        row_count=10,
        schema_json=[],
    )
    db_session.add(dataset)
    await db_session.commit()

    with patch("app.main.generate_synthetic_data") as mock_task:
        mock_task.delay = lambda *a, **kw: None

        resp = await auth_client.post(
            "/api/generate",
            json={
                "dataset_id": str(dataset.id),
                "row_count": 75,
                "model": "CTGAN",
            },
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["model_type"] == "CTGAN"
    assert "job_id" in body


@pytest.mark.anyio
async def test_generate_frontend_field_names_with_schema_overrides(auth_client, db_session, test_user):
    """schema_overrides from frontend must be accepted without 422."""
    from app.models import Dataset

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        original_filename="train.csv",
        s3_key="inputs/abc.csv",
        row_count=10,
        schema_json=[],
    )
    db_session.add(dataset)
    await db_session.commit()

    with patch("app.main.generate_synthetic_data") as mock_task:
        mock_task.delay = lambda *a, **kw: None

        resp = await auth_client.post(
            "/api/generate",
            json={
                "dataset_id": str(dataset.id),
                "row_count": 50,
                "model": "GaussianCopula",
                "schema_overrides": {"age": "numeric", "city": "categorical"},
            },
        )

    assert resp.status_code == 202, resp.text


@pytest.mark.anyio
async def test_generate_ctgan(auth_client, db_session, test_user):
    from app.models import Dataset

    dataset = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        original_filename="data.csv",
        s3_key="inputs/xyz.csv",
        row_count=10,
        schema_json=[],
    )
    db_session.add(dataset)
    await db_session.commit()

    with patch("app.main.generate_synthetic_data") as mock_task:
        mock_task.delay = lambda *a, **kw: None
        resp = await auth_client.post(
            "/api/generate",
            json={"dataset_id": str(dataset.id), "num_rows": 10, "model_type": "CTGAN"},
        )

    assert resp.status_code == 202
    assert resp.json()["model_type"] == "CTGAN"


@pytest.mark.anyio
async def test_generate_invalid_model(auth_client, db_session):
    dataset_id = str(uuid.uuid4())
    resp = await auth_client.post(
        "/api/generate",
        json={"dataset_id": dataset_id, "num_rows": 10, "model_type": "InvalidModel"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_generate_unknown_dataset(auth_client):
    resp = await auth_client.post(
        "/api/generate",
        json={"dataset_id": str(uuid.uuid4()), "num_rows": 10, "model_type": "GaussianCopula"},
    )
    assert resp.status_code == 404
