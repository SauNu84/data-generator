"""
Authentication utilities — JWT, password hashing, email tokens, Google OAuth.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# ─── Password hashing ─────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ─── SHA-256 helpers (tokens / API keys) ─────────────────────────────────────

def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ─── JWT ──────────────────────────────────────────────────────────────────────

def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": user_id, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token() -> str:
    """Return a cryptographically random 64-hex-char refresh token (raw, not JWT)."""
    return secrets.token_hex(32)


def decode_access_token(token: str) -> str:
    """Return user_id str or raise JWTError."""
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise JWTError("Not an access token")
    sub = payload.get("sub")
    if sub is None:
        raise JWTError("Missing sub claim")
    return sub


# ─── Email confirmation tokens ───────────────────────────────────────────────

_serializer = URLSafeTimedSerializer(settings.jwt_secret_key, salt="email-confirm")


def create_email_token(email: str) -> str:
    return _serializer.dumps(email)


def verify_email_token(token: str) -> str:
    """Return email or raise SignatureExpired / BadSignature."""
    return _serializer.loads(token, max_age=settings.email_token_expire_hours * 3600)


# ─── Google OAuth 2.0 ────────────────────────────────────────────────────────

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def build_google_auth_url(state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


async def exchange_google_code(code: str) -> dict:
    """Exchange auth code for user info. Returns dict with sub, email, email_verified."""
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()

        user_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        user_resp.raise_for_status()
        return user_resp.json()
