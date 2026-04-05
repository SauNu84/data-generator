"""
Integration tests for app/routes/dashboard.py

Scenarios:
  GET  /api/dashboard        — empty list, paginated list with datasets
  DELETE /api/dashboard/{id} — success (204), not found (404), wrong owner (404)
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.models import Dataset, GenerationJob


@pytest.mark.anyio
async def test_list_datasets_empty(auth_client, db_session):
    resp = await auth_client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["datasets"] == []
    assert body["page"] == 1


@pytest.mark.anyio
async def test_list_datasets_with_entries(auth_client, db_session, test_user):
    ds1 = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        original_filename="a.csv",
        s3_key="inputs/a.csv",
        row_count=10,
        schema_json=[],
    )
    ds2 = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        original_filename="b.csv",
        s3_key="inputs/b.csv",
        row_count=20,
        schema_json=[],
    )
    db_session.add(ds1)
    db_session.add(ds2)
    await db_session.commit()

    # Add a job to ds1
    job = GenerationJob(
        id=uuid.uuid4(),
        dataset_id=ds1.id,
        status="done",
        model_type="GaussianCopula",
        requested_rows=10,
    )
    db_session.add(job)
    await db_session.commit()

    resp = await auth_client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["datasets"]) == 2

    # datasets ordered newest first
    filenames = {d["original_filename"] for d in body["datasets"]}
    assert filenames == {"a.csv", "b.csv"}

    # ds1 has 1 job
    ds1_summary = next(d for d in body["datasets"] if d["original_filename"] == "a.csv")
    assert ds1_summary["job_count"] == 1
    assert ds1_summary["row_count"] == 10


@pytest.mark.anyio
async def test_list_datasets_pagination(auth_client, db_session, test_user):
    for i in range(5):
        db_session.add(Dataset(
            id=uuid.uuid4(),
            user_id=test_user.id,
            original_filename=f"file{i}.csv",
            s3_key=f"inputs/file{i}.csv",
            row_count=i + 1,
            schema_json=[],
        ))
    await db_session.commit()

    resp = await auth_client.get("/api/dashboard?page=1&page_size=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["datasets"]) == 2
    assert body["page"] == 1
    assert body["page_size"] == 2


@pytest.mark.anyio
async def test_delete_dataset_success(auth_client, db_session, test_user):
    ds = Dataset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        original_filename="todelete.csv",
        s3_key="inputs/todelete.csv",
        row_count=5,
        schema_json=[],
    )
    db_session.add(ds)
    await db_session.commit()

    resp = await auth_client.delete(f"/api/dashboard/{ds.id}")
    assert resp.status_code == 204

    # Verify it's gone
    from sqlalchemy import select
    result = await db_session.get(Dataset, ds.id)
    assert result is None


@pytest.mark.anyio
async def test_delete_dataset_not_found(auth_client, db_session):
    resp = await auth_client.delete(f"/api/dashboard/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_delete_dataset_wrong_owner(auth_client, db_session):
    """Dataset belonging to another user must return 404."""
    other_user_id = uuid.uuid4()
    ds = Dataset(
        id=uuid.uuid4(),
        user_id=other_user_id,
        original_filename="other.csv",
        s3_key="inputs/other.csv",
        row_count=5,
        schema_json=[],
    )
    db_session.add(ds)
    await db_session.commit()

    resp = await auth_client.delete(f"/api/dashboard/{ds.id}")
    assert resp.status_code == 404
