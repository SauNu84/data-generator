"""
API Key management routes (Pro tier only):
  POST   /api/keys        — create a new API key
  GET    /api/keys        — list keys with usage stats
  DELETE /api/keys/{id}   — revoke a key
"""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import sha256_hex
from app.database import get_db
from app.deps import get_current_user, require_pro
from app.models import ApiKey, User
from app.schemas import ApiKeyCreatedResponse, ApiKeyCreateRequest, ApiKeyResponse

router = APIRouter(prefix="/api/keys", tags=["api-keys"])


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    body: ApiKeyCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_pro),
):
    raw_key = f"sdg_live_{secrets.token_hex(32)}"
    prefix = raw_key[:16]  # "sdg_live_" + first 7 hex chars

    key = ApiKey(
        user_id=user.id,
        key_prefix=prefix,
        key_hash=sha256_hex(raw_key),
        name=body.name,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)

    return ApiKeyCreatedResponse(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        request_count=key.request_count,
        last_used_at=key.last_used_at,
        revoked=key.revoked,
        created_at=key.created_at,
        key=raw_key,  # only returned once
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = await db.scalars(
        select(ApiKey)
        .where(ApiKey.user_id == user.id, ApiKey.revoked.is_(False))
        .order_by(ApiKey.created_at.desc())
    )
    return list(rows)


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    key = await db.get(ApiKey, key_id)
    if not key or key.user_id != user.id:
        raise HTTPException(status_code=404, detail="API key not found.")
    key.revoked = True
    await db.commit()
