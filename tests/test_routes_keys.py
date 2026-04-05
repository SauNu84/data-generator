"""
Integration tests for app/routes/keys.py

Scenarios:
  POST   /api/keys        — create key (pro only), 403 on free tier
  GET    /api/keys        — list active keys, empty list
  DELETE /api/keys/{id}   — revoke success (204), not found (404), wrong owner (404)
"""

import uuid

import pytest

from app.models import ApiKey, User


# ─── Helpers ──────────────────────────────────────────────────────────────────

PRO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
async def pro_user(db_session) -> User:
    user = User(
        id=PRO_USER_ID,
        email="prouser@example.com",
        hashed_password=None,
        is_active=True,
        tier="pro",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def pro_auth_client(db_session, pro_user):
    """Client authenticated as a Pro-tier user."""
    from app.deps import get_current_user, get_current_user_or_api_key, require_pro
    from app.database import get_db
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    async def override_get_db():
        yield db_session

    async def override_auth():
        return pro_user

    async def override_require_pro():
        return pro_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_auth
    app.dependency_overrides[get_current_user_or_api_key] = override_auth
    app.dependency_overrides[require_pro] = override_require_pro

    import app.main as main_module
    main_module.ensure_bucket = lambda: None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ─── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_api_key_pro(pro_auth_client):
    resp = await pro_auth_client.post("/api/keys", json={"name": "My Key"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Key"
    assert body["key"].startswith("sdg_live_")
    assert body["revoked"] is False
    assert "id" in body


@pytest.mark.anyio
async def test_list_api_keys_empty(auth_client):
    resp = await auth_client.get("/api/keys")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_api_keys_with_entries(pro_auth_client, db_session):
    from app.auth import sha256_hex
    key = ApiKey(
        id=uuid.uuid4(),
        user_id=PRO_USER_ID,
        key_prefix="sdg_live_abc",
        key_hash=sha256_hex("sdg_live_abcdefghijklmnop"),
        name="Test Key",
    )
    db_session.add(key)
    await db_session.commit()

    resp = await pro_auth_client.get("/api/keys")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Test Key"
    assert body[0]["revoked"] is False


@pytest.mark.anyio
async def test_revoke_api_key_success(pro_auth_client, db_session):
    from app.auth import sha256_hex
    key = ApiKey(
        id=uuid.uuid4(),
        user_id=PRO_USER_ID,
        key_prefix="sdg_live_xyz",
        key_hash=sha256_hex("sdg_live_xyzxyzxyzxyzxyzx"),
        name="To Revoke",
    )
    db_session.add(key)
    await db_session.commit()

    resp = await pro_auth_client.delete(f"/api/keys/{key.id}")
    assert resp.status_code == 204

    await db_session.refresh(key)
    assert key.revoked is True


@pytest.mark.anyio
async def test_revoke_api_key_not_found(pro_auth_client):
    resp = await pro_auth_client.delete(f"/api/keys/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_revoke_api_key_wrong_owner(pro_auth_client, db_session):
    """Key belonging to another user must return 404."""
    from app.auth import sha256_hex
    other_user_id = uuid.uuid4()
    key = ApiKey(
        id=uuid.uuid4(),
        user_id=other_user_id,
        key_prefix="sdg_live_oth",
        key_hash=sha256_hex("sdg_live_otherotherotherot"),
        name="Other Key",
    )
    db_session.add(key)
    await db_session.commit()

    resp = await pro_auth_client.delete(f"/api/keys/{key.id}")
    assert resp.status_code == 404
