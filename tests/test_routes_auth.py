"""
Integration tests for app/routes/auth.py — SAU-116

Coverage target: ≥90% on app/routes/auth.py

Scenarios:
  POST /api/auth/register       — success (201), duplicate email (409), short password (422)
  GET  /api/auth/verify-email   — valid token, expired token, bad signature token, unknown email
  POST /api/auth/login          — success, wrong password (401), unknown email (401), inactive user (403)
  POST /api/auth/refresh        — success + token rotation, revoked token (401), expired token (401),
                                  missing field (422), unknown token (401)
  POST /api/auth/logout         — clears refresh token; graceful on unknown token; no refresh_token body
  GET  /api/auth/google         — redirects to Google
  GET  /api/auth/google/callback — creates new user, logs in existing user by google_sub,
                                   links existing email user, fails on bad code, fails on incomplete profile
  GET  /api/auth/me             — authenticated user returned, unauthenticated → 401
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.auth import create_access_token, create_email_token, create_refresh_token, sha256_hex
from app.config import settings
from app.models import RefreshToken, User


# SQLite (used in tests via aiosqlite) strips timezone info from DateTime columns,
# returning naive datetimes. The route compares `record.expires_at` against
# `datetime.now(timezone.utc)` (aware), which would raise TypeError.
# This fixture patches datetime.now in the route to return a naive UTC datetime
# so the comparison is always naive vs naive in the test environment.
@pytest.fixture(autouse=True)
def patch_route_datetime_now(monkeypatch):
    original_datetime = datetime

    class _NaiveDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return original_datetime.utcnow()

    monkeypatch.setattr("app.routes.auth.datetime", _NaiveDatetime)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _auth_headers(user_id: str) -> dict:
    token = create_access_token(str(user_id))
    return {"Authorization": f"Bearer {token}"}


async def _create_user(db_session, email: str, password_hash: str = "$2b$12$fakehash",
                       is_active: bool = True, google_sub: str | None = None,
                       is_email_verified: bool = False) -> User:
    user = User(
        email=email,
        hashed_password=password_hash,
        is_active=is_active,
        google_sub=google_sub,
        is_email_verified=is_email_verified,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _create_refresh_token(db_session, user_id: uuid.UUID,
                                 raw_token: str | None = None,
                                 revoked: bool = False,
                                 expired: bool = False) -> str:
    raw = raw_token or create_refresh_token()
    # SQLite (used in tests) returns naive datetimes; store naive to avoid
    # "can't compare offset-naive and offset-aware datetimes" in the route.
    now_naive = datetime.utcnow()
    expires_at = (
        now_naive - timedelta(days=1)
        if expired
        else now_naive + timedelta(days=30)
    )
    record = RefreshToken(
        user_id=user_id,
        token_hash=sha256_hex(raw),
        expires_at=expires_at,
        revoked=revoked,
    )
    db_session.add(record)
    await db_session.commit()
    return raw


# ─── POST /api/auth/register ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_success(client, db_session):
    with patch("app.routes.auth.hash_password", return_value="$2b$12$fakehash"), \
         patch("app.routes.auth.verify_password", return_value=True):
        resp = await client.post("/api/auth/register", json={
            "email": "newuser@example.com",
            "password": "securepass123",
        })
    assert resp.status_code == 201
    data = resp.json()
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "newuser@example.com"
    assert data["user"]["tier"] == "free"


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(client, db_session):
    with patch("app.routes.auth.hash_password", return_value="$2b$12$fakehash"):
        await client.post("/api/auth/register", json={
            "email": "dup@example.com",
            "password": "securepass123",
        })
        resp = await client.post("/api/auth/register", json={
            "email": "dup@example.com",
            "password": "anotherpass123",
        })
    assert resp.status_code == 409
    assert "already registered" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_short_password_returns_422(client):
    resp = await client.post("/api/auth/register", json={
        "email": "user@example.com",
        "password": "short",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_email_normalised_to_lowercase(client, db_session):
    with patch("app.routes.auth.hash_password", return_value="$2b$12$fakehash"):
        resp = await client.post("/api/auth/register", json={
            "email": "UPPER@EXAMPLE.COM",
            "password": "securepass123",
        })
    assert resp.status_code == 201
    assert resp.json()["user"]["email"] == "upper@example.com"


# ─── GET /api/auth/verify-email ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_email_valid_token(client, db_session):
    with patch("app.routes.auth.hash_password", return_value="$2b$12$fakehash"):
        await client.post("/api/auth/register", json={
            "email": "verify@example.com",
            "password": "securepass123",
        })

    token = create_email_token("verify@example.com")
    resp = await client.get(f"/api/auth/verify-email?token={token}", follow_redirects=False)
    # Should redirect to dashboard
    assert resp.status_code in (302, 307)
    assert "dashboard" in resp.headers["location"]


@pytest.mark.asyncio
async def test_verify_email_expired_token_returns_400(client, db_session):
    import time as time_module
    past = time_module.time() - (settings.email_token_expire_hours * 3600 + 1)
    with patch("itsdangerous.timed.time") as mock_time:
        mock_time.return_value = past
        token = create_email_token("expired@example.com")

    resp = await client.get(f"/api/auth/verify-email?token={token}")
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_email_bad_signature_returns_400(client):
    resp = await client.get("/api/auth/verify-email?token=bad.token.value")
    assert resp.status_code == 400
    assert "Invalid" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_verify_email_unknown_user_returns_404(client):
    token = create_email_token("nobody@example.com")
    resp = await client.get(f"/api/auth/verify-email?token={token}")
    assert resp.status_code == 404


# ─── POST /api/auth/login ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_success(client, db_session):
    await _create_user(db_session, "login@example.com")
    with patch("app.routes.auth.verify_password", return_value=True):
        resp = await client.post("/api/auth/login", json={
            "email": "login@example.com",
            "password": "anypassword",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["user"]["email"] == "login@example.com"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client, db_session):
    await _create_user(db_session, "wrongpw@example.com")
    with patch("app.routes.auth.verify_password", return_value=False):
        resp = await client.post("/api/auth/login", json={
            "email": "wrongpw@example.com",
            "password": "badpassword",
        })
    assert resp.status_code == 401
    assert "Invalid credentials" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_login_unknown_email_returns_401(client, db_session):
    resp = await client.post("/api/auth/login", json={
        "email": "nobody@example.com",
        "password": "somepassword",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user_returns_403(client, db_session):
    await _create_user(db_session, "inactive@example.com", is_active=False)
    with patch("app.routes.auth.verify_password", return_value=True):
        resp = await client.post("/api/auth/login", json={
            "email": "inactive@example.com",
            "password": "anypassword",
        })
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_login_oauth_only_user_no_password_returns_401(client, db_session):
    """User created via OAuth has no hashed_password — login must fail gracefully."""
    user = User(email="oauth-only@example.com", hashed_password=None, google_sub="google-123")
    db_session.add(user)
    await db_session.commit()

    resp = await client.post("/api/auth/login", json={
        "email": "oauth-only@example.com",
        "password": "anypassword",
    })
    assert resp.status_code == 401


# ─── POST /api/auth/refresh ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_success_rotates_token(client, db_session):
    user = await _create_user(db_session, "refresh@example.com")
    raw = await _create_refresh_token(db_session, user.id)

    resp = await client.post("/api/auth/refresh", json={"refresh_token": raw})
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"]
    new_raw = data["refresh_token"]
    assert new_raw != raw  # rotated

    # Old token must be revoked
    record = await db_session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw))
    )
    assert record.revoked is True


@pytest.mark.asyncio
async def test_refresh_revoked_token_returns_401(client, db_session):
    user = await _create_user(db_session, "revoked@example.com")
    raw = await _create_refresh_token(db_session, user.id, revoked=True)

    resp = await client.post("/api/auth/refresh", json={"refresh_token": raw})
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower() or "Invalid" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_refresh_expired_token_returns_401(client, db_session):
    user = await _create_user(db_session, "expired-refresh@example.com")
    raw = await _create_refresh_token(db_session, user.id, expired=True)

    resp = await client.post("/api/auth/refresh", json={"refresh_token": raw})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_refresh_unknown_token_returns_401(client, db_session):
    resp = await client.post("/api/auth/refresh", json={"refresh_token": "completely-unknown-token"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_missing_field_returns_422(client):
    resp = await client.post("/api/auth/refresh", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_refresh_inactive_user_returns_401(client, db_session):
    user = await _create_user(db_session, "inactive-refresh@example.com", is_active=False)
    raw = await _create_refresh_token(db_session, user.id)

    resp = await client.post("/api/auth/refresh", json={"refresh_token": raw})
    assert resp.status_code == 401


# ─── POST /api/auth/logout ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(client, db_session):
    user = await _create_user(db_session, "logout@example.com")
    raw = await _create_refresh_token(db_session, user.id)

    resp = await client.post("/api/auth/logout", json={"refresh_token": raw})
    assert resp.status_code == 204

    record = await db_session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw))
    )
    assert record.revoked is True


@pytest.mark.asyncio
async def test_logout_unknown_token_is_graceful(client):
    resp = await client.post("/api/auth/logout", json={"refresh_token": "unknown-token"})
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_logout_no_refresh_token_in_body_is_graceful(client):
    resp = await client.post("/api/auth/logout", json={})
    assert resp.status_code == 204


# ─── GET /api/auth/google ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_google_login_redirects_to_google(client):
    resp = await client.get("/api/auth/google", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "accounts.google.com" in resp.headers["location"]


# ─── GET /api/auth/google/callback ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_google_callback_creates_new_user(client, db_session):
    mock_userinfo = {
        "sub": "google-sub-new",
        "email": "newgoogle@example.com",
        "email_verified": True,
    }
    with patch("app.routes.auth.exchange_google_code", new_callable=AsyncMock, return_value=mock_userinfo):
        resp = await client.get("/api/auth/google/callback?code=valid-code", follow_redirects=False)

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert "access_token=" in location
    assert "refresh_token=" in location

    # User was created
    user = await db_session.scalar(select(User).where(User.email == "newgoogle@example.com"))
    assert user is not None
    assert user.google_sub == "google-sub-new"
    assert user.is_email_verified is True


@pytest.mark.asyncio
async def test_google_callback_logs_in_existing_user_by_sub(client, db_session):
    user = await _create_user(db_session, "existing-google@example.com", google_sub="google-sub-existing")
    mock_userinfo = {
        "sub": "google-sub-existing",
        "email": "existing-google@example.com",
        "email_verified": True,
    }
    with patch("app.routes.auth.exchange_google_code", new_callable=AsyncMock, return_value=mock_userinfo):
        resp = await client.get("/api/auth/google/callback?code=valid-code", follow_redirects=False)

    assert resp.status_code in (302, 307)
    # No new user was created
    users = (await db_session.execute(
        select(User).where(User.email == "existing-google@example.com")
    )).scalars().all()
    assert len(users) == 1


@pytest.mark.asyncio
async def test_google_callback_links_existing_email_user(client, db_session):
    """Email-only user gets google_sub linked on first OAuth sign-in."""
    user = await _create_user(db_session, "link@example.com")
    mock_userinfo = {
        "sub": "google-sub-link",
        "email": "link@example.com",
        "email_verified": True,
    }
    with patch("app.routes.auth.exchange_google_code", new_callable=AsyncMock, return_value=mock_userinfo):
        resp = await client.get("/api/auth/google/callback?code=valid-code", follow_redirects=False)

    assert resp.status_code in (302, 307)
    await db_session.refresh(user)
    assert user.google_sub == "google-sub-link"
    assert user.is_email_verified is True


@pytest.mark.asyncio
async def test_google_callback_bad_code_returns_400(client, db_session):
    with patch("app.routes.auth.exchange_google_code", new_callable=AsyncMock,
               side_effect=Exception("OAuth failed")):
        resp = await client.get("/api/auth/google/callback?code=bad-code")
    assert resp.status_code == 400
    assert "OAuth exchange failed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_google_callback_incomplete_profile_returns_400(client, db_session):
    """Missing sub/email in Google userinfo → 400."""
    mock_userinfo = {"email_verified": True}  # no sub, no email
    with patch("app.routes.auth.exchange_google_code", new_callable=AsyncMock, return_value=mock_userinfo):
        resp = await client.get("/api/auth/google/callback?code=incomplete-code")
    assert resp.status_code == 400
    assert "Incomplete" in resp.json()["detail"]


# ─── GET /api/auth/me ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_returns_current_user(client, db_session):
    user = await _create_user(db_session, "me@example.com")

    # Override get_current_user: SQLite + Uuid(as_uuid=True) can't look up by
    # a string primary key (no .hex attribute), so we bypass the DB lookup.
    from app.deps import get_current_user
    from app.main import app

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        resp = await client.get("/api/auth/me")
    finally:
        del app.dependency_overrides[get_current_user]

    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "me@example.com"
    assert data["tier"] == "free"
    assert "id" in data


@pytest.mark.asyncio
async def test_me_unauthenticated_returns_401(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_invalid_token_returns_401(client):
    resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.real.token"})
    assert resp.status_code == 401
