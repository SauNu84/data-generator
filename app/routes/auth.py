"""
Auth routes:
  POST /api/auth/register            — email + password registration
  GET  /api/auth/verify-email        — email confirmation link
  POST /api/auth/login               — email + password login
  POST /api/auth/refresh             — refresh access token
  POST /api/auth/logout              — revoke refresh token
  GET  /api/auth/google              — redirect to Google OAuth
  GET  /api/auth/google/callback     — Google OAuth callback
  GET  /api/auth/me                  — current user profile
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.database import get_db
from app.deps import get_current_user
from app.models import RefreshToken, User
from app.schemas import (
    AuthTokenResponse,
    LoginRequest,
    RegisterRequest,
    UserProfile,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _make_refresh_token_record(user_id: uuid.UUID, raw_token: str) -> RefreshToken:
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    return RefreshToken(
        user_id=user_id,
        token_hash=sha256_hex(raw_token),
        expires_at=expires_at,
    )


# ─── Register ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=AuthTokenResponse, status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.scalar(select(User).where(User.email == body.email.lower()))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered.")

    user = User(
        email=body.email.lower(),
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.flush()  # get user.id before commit

    # Issue tokens immediately (unverified — email confirmation required for API keys)
    raw_refresh = create_refresh_token()
    db.add(_make_refresh_token_record(user.id, raw_refresh))
    await db.commit()
    await db.refresh(user)

    # TODO: send confirmation email (requires email service integration)
    # email_token = create_email_token(user.email)
    # send_email(user.email, f"{settings.backend_base_url}/api/auth/verify-email?token={email_token}")

    return AuthTokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=raw_refresh,
        token_type="bearer",
        user=UserProfile.model_validate(user),
    )


# ─── Email Verification ───────────────────────────────────────────────────────

@router.get("/verify-email")
async def verify_email(token: str = Query(...), db: AsyncSession = Depends(get_db)):
    try:
        email = verify_email_token(token)
    except SignatureExpired:
        raise HTTPException(status_code=400, detail="Verification link has expired.")
    except BadSignature:
        raise HTTPException(status_code=400, detail="Invalid verification token.")

    user = await db.scalar(select(User).where(User.email == email.lower()))
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.is_email_verified = True
    await db.commit()
    return RedirectResponse(url=f"{settings.app_base_url}/dashboard?verified=1")


# ─── Login ────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=AuthTokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled.")

    raw_refresh = create_refresh_token()
    db.add(_make_refresh_token_record(user.id, raw_refresh))
    await db.commit()

    return AuthTokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=raw_refresh,
        token_type="bearer",
        user=UserProfile.model_validate(user),
    )


# ─── Refresh ──────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh_token(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    raw_token = body.get("refresh_token", "")
    if not raw_token:
        raise HTTPException(status_code=422, detail="refresh_token required.")

    token_hash = sha256_hex(raw_token)
    record = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if not record or record.revoked:
        raise HTTPException(status_code=401, detail="Invalid or revoked refresh token.")
    if record.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token has expired.")

    user = await db.get(User, record.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled.")

    # Rotate: revoke old, issue new
    record.revoked = True
    new_raw = create_refresh_token()
    db.add(_make_refresh_token_record(user.id, new_raw))
    await db.commit()

    return AuthTokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=new_raw,
        token_type="bearer",
        user=UserProfile.model_validate(user),
    )


# ─── Logout ───────────────────────────────────────────────────────────────────

@router.post("/logout", status_code=204)
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    raw_token = body.get("refresh_token", "")
    if raw_token:
        token_hash = sha256_hex(raw_token)
        record = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        if record:
            record.revoked = True
            await db.commit()


# ─── Google OAuth ─────────────────────────────────────────────────────────────

@router.get("/google")
async def google_login():
    state = secrets.token_hex(16)
    # NOTE: In production store state in Redis with TTL to prevent CSRF.
    return RedirectResponse(url=build_google_auth_url(state))


@router.get("/google/callback")
async def google_callback(code: str = Query(...), db: AsyncSession = Depends(get_db)):
    try:
        userinfo = await exchange_google_code(code)
    except Exception:
        raise HTTPException(status_code=400, detail="Google OAuth exchange failed.")

    google_sub = userinfo.get("sub")
    email = (userinfo.get("email") or "").lower()
    email_verified = userinfo.get("email_verified", False)

    if not google_sub or not email:
        raise HTTPException(status_code=400, detail="Incomplete Google profile.")

    # Find or create user
    user = await db.scalar(select(User).where(User.google_sub == google_sub))
    if not user:
        user = await db.scalar(select(User).where(User.email == email))
    if not user:
        user = User(email=email, google_sub=google_sub, is_email_verified=email_verified)
        db.add(user)
        await db.flush()
    else:
        if not user.google_sub:
            user.google_sub = google_sub
        if email_verified:
            user.is_email_verified = True

    raw_refresh = create_refresh_token()
    db.add(_make_refresh_token_record(user.id, raw_refresh))
    await db.commit()

    access = create_access_token(str(user.id))
    # Redirect to frontend with tokens in fragment (SPA handles storage)
    redirect_url = (
        f"{settings.app_base_url}/auth/callback"
        f"#access_token={access}&refresh_token={raw_refresh}"
    )
    return RedirectResponse(url=redirect_url)


# ─── Current User ─────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserProfile)
async def me(current_user: User = Depends(get_current_user)):
    return UserProfile.model_validate(current_user)
