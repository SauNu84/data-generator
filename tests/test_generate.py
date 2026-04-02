"""Tests for POST /api/generate."""

import uuid
from unittest.mock import patch

import pytest


@pytest.mark.anyio
async def test_generate_happy_path(client, db_session, sample_csv_bytes):
    from app.models import Dataset

    # Insert a dataset first
    dataset = Dataset(
        id=uuid.uuid4(),
        original_filename="train.csv",
        s3_key="inputs/abc.csv",
        row_count=10,
        schema_json=[{"name": "age", "sdtype": "numerical", "dtype": "int64"}],
    )
    db_session.add(dataset)
    await db_session.commit()

    with patch("app.main.generate_synthetic_data") as mock_task:
        mock_task.delay = lambda *a, **kw: None

        resp = await client.post(
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
async def test_generate_ctgan(client, db_session):
    from app.models import Dataset

    dataset = Dataset(
        id=uuid.uuid4(),
        original_filename="data.csv",
        s3_key="inputs/xyz.csv",
        row_count=10,
        schema_json=[],
    )
    db_session.add(dataset)
    await db_session.commit()

    with patch("app.main.generate_synthetic_data") as mock_task:
        mock_task.delay = lambda *a, **kw: None
        resp = await client.post(
            "/api/generate",
            json={"dataset_id": str(dataset.id), "num_rows": 10, "model_type": "CTGAN"},
        )

    assert resp.status_code == 202
    assert resp.json()["model_type"] == "CTGAN"


@pytest.mark.anyio
async def test_generate_invalid_model(client, db_session):
    dataset_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/generate",
        json={"dataset_id": dataset_id, "num_rows": 10, "model_type": "InvalidModel"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_generate_unknown_dataset(client):
    resp = await client.post(
        "/api/generate",
        json={"dataset_id": str(uuid.uuid4()), "num_rows": 10, "model_type": "GaussianCopula"},
    )
    assert resp.status_code == 404
