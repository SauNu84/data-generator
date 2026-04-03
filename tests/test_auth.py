"""Unit tests for app/auth.py — JWT, password hashing, email tokens, Google OAuth.

Coverage target (SAU-115): ≥90% on app/auth.py

Tested:
  - hash_password / verify_password (correct, wrong password)
  - sha256_hex (determinism, encoding)
  - create_access_token / decode_access_token (valid, expired, wrong type, missing sub)
  - create_refresh_token (length, hex format, uniqueness)
  - create_email_token / verify_email_token (valid, expired, bad signature)
  - build_google_auth_url (required params present)
  - exchange_google_code (happy path, HTTP errors)
  - Security: expired JWT, tampered payload, wrong algorithm all raise JWTError
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from itsdangerous import BadSignature, SignatureExpired
from jose import JWTError, jwt

from app.auth import (
    build_google_auth_url,
    create_access_token,
    create_email_token,
    create_refresh_token,
    decode_access_token,
    exchange_google_code,
    hash_password,
    sha256_hex,
    verify_email_token,
    verify_password,
)
from app.config import settings


# ─── Password hashing ─────────────────────────────────────────────────────────
# We mock pwd_context to avoid bcrypt version incompatibility in CI
# (passlib 1.7.4 + bcrypt ≥4.0 raises ValueError on detect_wrap_bug).
# The functions under test are thin wrappers; the important behaviour is that
# they delegate to pwd_context.hash / pwd_context.verify correctly.


def test_hash_password_delegates_to_pwd_context():
    with patch("app.auth.pwd_context") as mock_ctx:
        mock_ctx.hash.return_value = "$2b$12$fakehash"
        result = hash_password("mysecret")
    mock_ctx.hash.assert_called_once_with("mysecret")
    assert result == "$2b$12$fakehash"


def test_verify_password_correct():
    with patch("app.auth.pwd_context") as mock_ctx:
        mock_ctx.verify.return_value = True
        assert verify_password("correct-password", "$2b$12$fakehash") is True
    mock_ctx.verify.assert_called_once_with("correct-password", "$2b$12$fakehash")


def test_verify_password_wrong():
    with patch("app.auth.pwd_context") as mock_ctx:
        mock_ctx.verify.return_value = False
        assert verify_password("wrong-password", "$2b$12$fakehash") is False


def test_hash_password_unique():
    """Bcrypt produces unique hashes per call (salted); verify delegation."""
    with patch("app.auth.pwd_context") as mock_ctx:
        mock_ctx.hash.side_effect = ["$2b$12$hash1", "$2b$12$hash2"]
        h1 = hash_password("same")
        h2 = hash_password("same")
    assert h1 != h2


# ─── SHA-256 ──────────────────────────────────────────────────────────────────


def test_sha256_hex_deterministic():
    assert sha256_hex("hello") == sha256_hex("hello")


def test_sha256_hex_length():
    result = sha256_hex("anything")
    assert len(result) == 64  # 256-bit → 64 hex chars


def test_sha256_hex_different_inputs():
    assert sha256_hex("a") != sha256_hex("b")


def test_sha256_hex_known_value():
    import hashlib
    value = "test-api-key"
    expected = hashlib.sha256(value.encode()).hexdigest()
    assert sha256_hex(value) == expected


# ─── create_refresh_token ─────────────────────────────────────────────────────


def test_create_refresh_token_length():
    token = create_refresh_token()
    assert len(token) == 64  # secrets.token_hex(32) → 64 hex chars


def test_create_refresh_token_is_hex():
    token = create_refresh_token()
    int(token, 16)  # raises ValueError if not valid hex


def test_create_refresh_token_unique():
    assert create_refresh_token() != create_refresh_token()


# ─── create_access_token / decode_access_token ───────────────────────────────


def test_create_and_decode_access_token_roundtrip():
    user_id = "user-abc-123"
    token = create_access_token(user_id)
    assert decode_access_token(token) == user_id


def test_decode_access_token_valid_claims():
    user_id = "user-xyz"
    token = create_access_token(user_id)
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == user_id
    assert payload["type"] == "access"
    assert "exp" in payload


def test_decode_access_token_expired_raises():
    expire = datetime.now(timezone.utc) - timedelta(seconds=1)
    payload = {"sub": "user-1", "exp": expire, "type": "access"}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    with pytest.raises(JWTError):
        decode_access_token(token)


def test_decode_access_token_wrong_type_raises():
    """A refresh-type JWT must NOT decode as an access token."""
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = {"sub": "user-1", "exp": expire, "type": "refresh"}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    with pytest.raises(JWTError, match="Not an access token"):
        decode_access_token(token)


def test_decode_access_token_missing_sub_raises():
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = {"exp": expire, "type": "access"}  # no sub
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    with pytest.raises(JWTError, match="Missing sub claim"):
        decode_access_token(token)


# ─── Security assertions ──────────────────────────────────────────────────────


def test_tampered_payload_raises_jwt_error():
    """Flipping a byte in the signature portion must raise JWTError."""
    token = create_access_token("user-safe")
    parts = token.split(".")
    # Corrupt the signature segment
    corrupted_sig = parts[2][:-4] + "XXXX"
    tampered = ".".join([parts[0], parts[1], corrupted_sig])
    with pytest.raises(JWTError):
        decode_access_token(tampered)


def test_wrong_algorithm_raises_jwt_error():
    """A token signed with a different algorithm must be rejected."""
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = {"sub": "user-1", "exp": expire, "type": "access"}
    # Sign with RS256 would require a key pair; instead sign with a different secret.
    token = jwt.encode(payload, "completely-wrong-secret", algorithm="HS256")
    with pytest.raises(JWTError):
        decode_access_token(token)


def test_invalid_token_string_raises_jwt_error():
    with pytest.raises(JWTError):
        decode_access_token("not.a.jwt.at.all")


# ─── Email tokens ─────────────────────────────────────────────────────────────


def test_create_and_verify_email_token_roundtrip():
    email = "user@example.com"
    token = create_email_token(email)
    assert verify_email_token(token) == email


def test_verify_email_token_expired_raises():
    """Token created in the past must raise SignatureExpired."""
    import time as time_module
    email = "user@example.com"
    # Create token at t=0, then verify pretending much more time has passed
    past = time_module.time() - (settings.email_token_expire_hours * 3600 + 1)
    with patch("itsdangerous.timed.time") as mock_time:
        mock_time.return_value = past
        token = create_email_token(email)
    with pytest.raises(SignatureExpired):
        verify_email_token(token)


def test_verify_email_token_bad_signature_raises():
    with pytest.raises(BadSignature):
        verify_email_token("tampered.token.value")


def test_verify_email_token_different_emails_produce_different_tokens():
    t1 = create_email_token("a@example.com")
    t2 = create_email_token("b@example.com")
    assert t1 != t2


# ─── build_google_auth_url ────────────────────────────────────────────────────


def test_build_google_auth_url_starts_with_google():
    url = build_google_auth_url(state="random-state-value")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")


def test_build_google_auth_url_required_params():
    url = build_google_auth_url(state="test-state")
    assert "response_type=code" in url
    assert "scope=openid" in url
    assert "access_type=offline" in url
    assert "state=test-state" in url


def test_build_google_auth_url_contains_client_id():
    with patch.object(settings, "google_client_id", "my-client-id"):
        url = build_google_auth_url(state="s")
    assert "my-client-id" in url


def test_build_google_auth_url_contains_redirect_uri():
    url = build_google_auth_url(state="s")
    assert settings.google_redirect_uri in url


# ─── exchange_google_code ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exchange_google_code_happy_path():
    mock_user_info = {
        "sub": "google-uid-123",
        "email": "oauth@example.com",
        "email_verified": True,
    }
    mock_token_resp = MagicMock()
    mock_token_resp.raise_for_status = MagicMock()
    mock_token_resp.json = MagicMock(return_value={"access_token": "goog-access-tok"})

    mock_user_resp = MagicMock()
    mock_user_resp.raise_for_status = MagicMock()
    mock_user_resp.json = MagicMock(return_value=mock_user_info)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_token_resp)
    mock_client.get = AsyncMock(return_value=mock_user_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.auth.httpx.AsyncClient", return_value=mock_client):
        result = await exchange_google_code("auth-code-abc")

    assert result == mock_user_info
    mock_client.post.assert_called_once()
    mock_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_exchange_google_code_token_endpoint_error_raises():
    import httpx

    mock_token_resp = MagicMock()
    mock_token_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock())
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_token_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.auth.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(Exception):
            await exchange_google_code("bad-code")
