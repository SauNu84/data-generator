"""
Integration tests for app/routes/dbt.py

Scenarios:
  POST /api/dbt/parse    — success, invalid yaml, free tier blocked
  POST /api/dbt/generate — success (202), free tier blocked, model not found, invalid yaml
"""

import uuid
from unittest.mock import patch

import pytest

from app.models import User


# ─── Fixtures ─────────────────────────────────────────────────────────────────

PRO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")

VALID_SCHEMA_YAML = """
version: 2
models:
  - name: orders
    columns:
      - name: order_id
        data_type: uuid
        tests:
          - unique
          - not_null
      - name: amount
        data_type: numeric
      - name: status
        data_type: varchar
"""


@pytest.fixture
async def pro_user_dbt(db_session) -> User:
    user = User(
        id=PRO_USER_ID,
        email="dbt_pro@example.com",
        hashed_password=None,
        is_active=True,
        tier="pro",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def pro_dbt_client(db_session, pro_user_dbt):
    from app.deps import get_current_user, get_current_user_or_api_key
    from app.database import get_db
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    async def override_get_db():
        yield db_session

    async def override_auth():
        return pro_user_dbt

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_auth
    app.dependency_overrides[get_current_user_or_api_key] = override_auth

    import app.main as main_module
    main_module.ensure_bucket = lambda: None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ─── /api/dbt/parse ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_dbt_parse_success(pro_dbt_client):
    resp = await pro_dbt_client.post(
        "/api/dbt/parse", json={"schema_yaml": VALID_SCHEMA_YAML}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["models"]) == 1
    model = body["models"][0]
    assert model["name"] == "orders"
    assert model["column_count"] == 3
    assert "columns" in model["sdv_metadata"]


@pytest.mark.anyio
async def test_dbt_parse_invalid_yaml(pro_dbt_client):
    resp = await pro_dbt_client.post(
        "/api/dbt/parse", json={"schema_yaml": "key: [unclosed"}
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_dbt_parse_missing_version(pro_dbt_client):
    bad_yaml = "models:\n  - name: foo\n    columns: []\n"
    resp = await pro_dbt_client.post("/api/dbt/parse", json={"schema_yaml": bad_yaml})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_dbt_parse_free_tier_blocked(auth_client):
    """Free-tier users should get 402 on /api/dbt/parse."""
    resp = await auth_client.post(
        "/api/dbt/parse", json={"schema_yaml": VALID_SCHEMA_YAML}
    )
    assert resp.status_code == 402


@pytest.mark.anyio
async def test_dbt_parse_model_no_columns_skipped(pro_dbt_client):
    """Models with no columns are skipped and a warning is added."""
    yaml = "version: 2\nmodels:\n  - name: empty_model\n    columns: []\n"
    resp = await pro_dbt_client.post("/api/dbt/parse", json={"schema_yaml": yaml})
    assert resp.status_code == 200
    body = resp.json()
    # empty_model has no columns → skipped from previews
    assert body["models"] == []


# ─── /api/dbt/generate ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_dbt_generate_success(pro_dbt_client):
    with patch("app.routes.dbt.generate_synthetic_data") as mock_task:
        mock_task.delay = lambda *a, **kw: None
        resp = await pro_dbt_client.post(
            "/api/dbt/generate",
            json={
                "schema_yaml": VALID_SCHEMA_YAML,
                "model_name": "orders",
                "row_count": 100,
                "sdv_model": "GaussianCopula",
            },
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["model_name"] == "orders"
    assert body["row_count"] == 100
    assert body["status"] == "queued"
    assert "job_id" in body
    assert "dataset_id" in body


@pytest.mark.anyio
async def test_dbt_generate_free_tier_blocked(auth_client):
    resp = await auth_client.post(
        "/api/dbt/generate",
        json={
            "schema_yaml": VALID_SCHEMA_YAML,
            "model_name": "orders",
            "row_count": 100,
        },
    )
    assert resp.status_code == 402


@pytest.mark.anyio
async def test_dbt_generate_model_not_found(pro_dbt_client):
    resp = await pro_dbt_client.post(
        "/api/dbt/generate",
        json={
            "schema_yaml": VALID_SCHEMA_YAML,
            "model_name": "nonexistent_model",
            "row_count": 100,
        },
    )
    assert resp.status_code == 422
    assert "nonexistent_model" in resp.json()["detail"]


@pytest.mark.anyio
async def test_dbt_generate_invalid_yaml(pro_dbt_client):
    resp = await pro_dbt_client.post(
        "/api/dbt/generate",
        json={
            "schema_yaml": "bad: [yaml",
            "model_name": "orders",
            "row_count": 100,
        },
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_dbt_generate_model_no_columns(pro_dbt_client):
    yaml = "version: 2\nmodels:\n  - name: empty\n    columns: []\n"
    resp = await pro_dbt_client.post(
        "/api/dbt/generate",
        json={"schema_yaml": yaml, "model_name": "empty", "row_count": 50},
    )
    assert resp.status_code == 422
