# ADR-004: Auth & Account System

- **Status**: Accepted
- **Date**: 2026-04-02
- **Deciders**: Enterprise Architect, CTO
- **Issue**: SAU-106 (parent: SAU-105)

---

## Context

Phase 2 commercialises the Synthetic Data Generator. Every authenticated surface — web UI, API keys, Stripe billing, usage tracking — depends on a working identity layer first. This ADR defines:

1. How users authenticate (email/password + Google OAuth)
2. Session token strategy (JWT vs server-side sessions)
3. Token refresh and revocation design
4. Redis session store layout

**Constraints:**
- FastAPI + Python 3.11 stack (ADR-001)
- Redis already in stack (ADR-002)
- Must support both browser (cookie-based) and API consumer (Bearer token) flows
- Stateless tokens desirable for horizontal scaling; revocation capability required for security

---

## Decision Drivers

| Driver | Weight |
|--------|--------|
| Support browser (cookie) + API (Bearer) clients | High |
| Token revocation / forced logout (security) | High |
| Horizontal worker scaling compatibility | High |
| Google OAuth integration | High |
| Implementation speed (M1: Weeks 1–4) | High |
| Session hijacking attack surface | Medium |

---

## Options Considered

### Option 1: Pure Stateless JWT (no server-side store)

**Pros:**
- Zero server-side state — scales horizontally with no coordination
- Standard library support (`python-jose`, `PyJWT`)

**Cons:**
- **No revocation** — a stolen access token is valid until expiry; can't force-logout a compromised account
- Refresh token rotation requires a denylist anyway — effectively stateful at refresh time
- Logout is a client-side lie (delete cookie/header; token still valid)

**Verdict:** Unacceptable for a commercial product handling paid user data.

### Option 2: Server-side Sessions Only (Redis)

**Pros:**
- Instant revocation: delete session key from Redis
- Server controls session lifetime entirely

**Cons:**
- Every API request hits Redis — adds ~1ms latency, Redis becomes critical path
- Stateless API key consumers still need a separate mechanism
- Does not naturally map to OAuth token flows

**Verdict:** Works for browser auth but adds unnecessary Redis dependency for API-key consumers.

### Option 3: Short-lived JWT Access Tokens + Refresh Token Rotation (Redis denylist)

**Pros:**
- Access tokens (15 min TTL) are stateless — API requests don't hit Redis on every call
- Refresh tokens stored server-side in Redis → revocable
- Forced logout: delete refresh token from Redis; next refresh fails → access token expires in ≤15 min
- Supports both browser (HttpOnly cookie) and Bearer token (Authorization header) clients
- Refresh token rotation: each `/auth/refresh` issues a new refresh token and invalidates the old one (rotation defeats replay attacks)
- Aligns with Google OAuth token model

**Cons:**
- Access token can be used for up to 15 min after forced logout (acceptable window)
- Redis must be available for token refresh (already true for Celery)

**Verdict:** Correct design. Industry standard for this architecture profile.

---

## Decision

**Short-lived JWT Access Tokens (15 min) + Rotating Refresh Tokens (7 days, stored in Redis)**

---

## Auth Flow Design

### Email/Password Registration & Login

```
POST /auth/register
  Body: { email, password }
  → Hash password (bcrypt, cost 12)
  → INSERT users (email, hashed_password, is_active=true, tier='free')
  → Return: { access_token (JWT), refresh_token (opaque, stored in Redis) }

POST /auth/login
  Body: { email, password }
  → Lookup user, bcrypt.verify
  → Issue access_token + refresh_token
  → Set-Cookie: refresh_token (HttpOnly, Secure, SameSite=Strict, 7d)
  → Body: { access_token, expires_in: 900 }
```

### Google OAuth Flow

```
GET /auth/google
  → Redirect to Google OAuth2 consent screen
  → Scopes: openid email profile

GET /auth/google/callback?code=...
  → Exchange code for Google tokens (server-side, never expose client_secret)
  → GET https://www.googleapis.com/oauth2/v3/userinfo
  → UPSERT users (google_id, email, name, avatar_url)
    - If new user: tier='free', is_active=true
    - If existing user: update name/avatar, preserve tier
  → Issue access_token + refresh_token (same as email/password flow)
  → Redirect to /dashboard with access_token in URL fragment (#token=...)
    or set HttpOnly cookie if browser flow
```

### Token Refresh

```
POST /auth/refresh
  Body (or Cookie): { refresh_token }
  → Lookup key: "refresh:{token_hash}" in Redis
    - Not found → 401 Unauthorized (expired or revoked)
  → Delete old key (rotation: old token invalidated)
  → Issue new access_token + new refresh_token
  → Store new refresh token in Redis
  → Return: { access_token, refresh_token, expires_in: 900 }
```

### Logout / Revocation

```
POST /auth/logout
  → Extract refresh_token from cookie or body
  → DEL "refresh:{token_hash}" from Redis
  → Clear HttpOnly cookie
  → Return 204 No Content

Force-logout (admin / account compromise):
  → SCAN Redis for "refresh:{user_id}:*" pattern
  → DEL all matching keys
  → Next access token refresh fails → session expires in ≤15 min
```

---

## JWT Access Token Schema

```json
{
  "sub": "user_uuid",
  "email": "user@example.com",
  "tier": "free | pro | enterprise",
  "iat": 1712000000,
  "exp": 1712000900
}
```

**Signing:** HS256 with a 256-bit secret stored in `SECRET_KEY` env var. Rotate via key-versioning field `kid` in JWT header when needed.

---

## Refresh Token Storage — DB Primary (Implementation Update)

**Update 2026-04-02 (CTO):** Original design specified Redis as primary store. Implementation uses Postgres `refresh_tokens` table as primary store. This deviation is **accepted**. Rationale:

- Durable audit trail: token history survives Redis restarts
- Batch revocation by user via `UPDATE refresh_tokens SET revoked=true WHERE user_id=X`
- No Redis availability dependency for token refresh path
- `revoked` flag is functionally equivalent to Redis DEL

**Postgres schema (in `app/models.py`):**
```
refresh_tokens: id, user_id (FK→users), token_hash (SHA-256, unique), expires_at, revoked, created_at
```

**Redis still used for:** Celery broker (DB 0), result backend (DB 1). Redis DB 2 (session cache) is no longer required.

**Revocation pattern:**
```python
# Single token revocation
UPDATE refresh_tokens SET revoked=true WHERE token_hash=$hash

# Force-logout all sessions
UPDATE refresh_tokens SET revoked=true WHERE user_id=$user_id AND revoked=false
```

---

## FastAPI Middleware Design

```python
# Dependency injection pattern — no global middleware needed
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    payload = decode_jwt(token)           # raises 401 if expired/invalid
    user = await db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(401)
    return user

async def require_tier(tier: str):
    def _inner(user: User = Depends(get_current_user)):
        if TIER_ORDER[user.tier] < TIER_ORDER[tier]:
            raise HTTPException(403, "Upgrade required")
    return _inner
```

**Route examples:**
```python
@router.get("/datasets", dependencies=[Depends(get_current_user)])
@router.post("/datasets/{id}/jobs", dependencies=[Depends(require_tier("pro"))])
```

---

## Consequences

**Positive:**
- Stateless access token = no Redis hit on 95%+ of requests
- Refresh token revocation = credible security posture for paid users
- Google OAuth covers 60–70% of developer sign-ups (reduces password management)
- HttpOnly cookie protects browser clients from XSS token theft
- Tier claim embedded in JWT = no DB hit for tier enforcement on API routes

**Negative / Trade-offs:**
- 15-minute window between forced-logout and access token expiry (acceptable)
- Redis must be available for refresh; plan Redis Sentinel / AOF persistence before launch
- Google OAuth requires client_id + client_secret in env (managed secrets)

---

## Dependencies

- `python-jose[cryptography]` — JWT signing/validation
- `passlib[bcrypt]` — password hashing
- `authlib` — Google OAuth client (PKCE, token exchange)
- `redis-py` (async) — refresh token store (already in stack)

---

## Revisit Trigger

Revisit if: multi-tenant SSO requirement (Enterprise tier) materialises → evaluate Auth0/Okta integration. Trigger: first Enterprise customer contract.
