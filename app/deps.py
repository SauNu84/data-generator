"""
FastAPI dependency injection helpers.
"""

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import decode_access_token, sha256_hex
from app.database import get_db
from app.models import ApiKey, User

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Accept Bearer <JWT access token>."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    token = credentials.credentials
    try:
        user_id = decode_access_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found.")
    return user


async def get_current_user_or_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Accept JWT bearer OR X-API-Key header (for programmatic access).
    Used on routes that are reachable via both the web app and API.
    """
    # Try JWT first
    if credentials and credentials.scheme.lower() == "bearer":
        try:
            user_id = decode_access_token(credentials.credentials)
            user = await db.get(User, user_id)
            if user and user.is_active:
                return user
        except JWTError:
            pass

    # Try X-API-Key
    if x_api_key:
        key_hash = sha256_hex(x_api_key)
        api_key = await db.scalar(
            select(ApiKey).where(
                ApiKey.key_hash == key_hash,
                ApiKey.revoked.is_(False),
            )
        )
        if api_key:
            user = await db.get(User, api_key.user_id)
            if user and user.is_active:
                # Update last_used_at + request_count
                api_key.last_used_at = datetime.now(timezone.utc)
                api_key.request_count += 1
                await db.commit()
                return user

    raise HTTPException(status_code=401, detail="Not authenticated.")


async def require_pro(user: User = Depends(get_current_user)) -> User:
    if user.tier not in ("pro", "enterprise"):
        raise HTTPException(
            status_code=403,
            detail="This feature requires a Pro subscription.",
        )
    return user
