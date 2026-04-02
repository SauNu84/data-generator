# ADR-005: API Key Management

- **Status**: Accepted
- **Date**: 2026-04-02
- **Deciders**: Enterprise Architect, CTO
- **Issue**: SAU-106 (parent: SAU-105)

---

## Context

Phase 2 exposes a machine-to-machine API for developers and CI/CD pipelines. This requires:

1. API key generation with a prefix scheme (human-readable, easy to scan in logs)
2. Secure storage (only hash stored in DB; plaintext shown once at creation)
3. Rate limiting per tier (free: 60 req/min, pro: 600 req/min, enterprise: unlimited)
4. Tier enforcement middleware that maps key → user → tier
5. Usage metering for billing (calls per key per day)

**Constraints:**
- Redis already in stack (DB 0=Celery broker, DB 1=result backend, DB 2=sessions)
- FastAPI dependency-injection pattern established in ADR-004
- Keys must survive Redis restart (Redis is volatile; source of truth is PostgreSQL)

---

## Decision Drivers

| Driver | Weight |
|--------|--------|
| Security: plaintext key never stored | High |
| Rate limiting without DB hit on every request | High |
| Key revocation instant | High |
| Tier enforcement at middleware level (no per-route logic) | High |
| Usage metering for billing | High |
| Key prefix scannable in logs (grep-able) | Medium |

---

## API Key Format

### Prefix Scheme

```
sdg_live_{random_32_bytes_base62}
sdg_test_{random_32_bytes_base62}

Examples:
  sdg_live_4xK9mPqL2nRvBzYeWtUdAf8gCsHjNmQp
  sdg_test_7wE3vXaD6oTcBnYuFkPqLs9MrGhJiZmN
```

- `sdg_` — product namespace; easy to grep in logs, GH secret scanning catches leaked keys
- `live_` / `test_` — environment disambiguation; test keys have no billing impact
- 32 bytes of `secrets.token_urlsafe(32)` ≈ 256 bits of entropy — brute-force infeasible

### Storage Model

```
At creation:
  1. Generate full key: sdg_live_{token}
  2. Compute: key_hash = sha256(full_key)
  3. Store in api_keys table: { prefix="sdg_live_", key_hash, user_id, tier, ... }
  4. Return plaintext key to user ONCE — never stored, never retrievable
  5. Display: "Copy this key — you won't see it again"

At lookup:
  1. Compute sha256(incoming_key)
  2. SELECT * FROM api_keys WHERE key_hash = $1 AND is_active = true
  3. Cache result in Redis for 60s (key: "apikey:{key_hash}", value: user_id+tier JSON)
```

---

## Rate Limiting Design

### Algorithm: Redis Sliding Window

Sliding window counter is more accurate than fixed window and avoids burst spikes at window boundaries (token bucket also works but is harder to reason about for billing).

```
Key:    ratelimit:{key_hash}:{unix_minute}
Type:   string (integer counter)
TTL:    120s (two windows retained)

On each request:
  now_minute = int(time.time() / 60)
  INCR ratelimit:{key_hash}:{now_minute}
  EXPIRE ratelimit:{key_hash}:{now_minute} 120

  # Sliding window = current + previous minute (weighted by position within minute)
  prev_minute = now_minute - 1
  count_prev = GET ratelimit:{key_hash}:{prev_minute} or 0
  count_curr = GET ratelimit:{key_hash}:{now_minute}
  second_in_minute = time.time() % 60
  sliding = count_prev * (1 - second_in_minute / 60) + count_curr

  if sliding > tier_limit:
      return 429 Too Many Requests (Retry-After: 60 - second_in_minute)
```

**Tier limits:**

| Tier       | Requests/min | Burst cap |
|------------|-------------|-----------|
| free       | 20          | 20        |
| pro        | 200         | 200       |
| enterprise | unlimited   | —         |

> Note: free tier also has 10 generations/month hard cap (enforced at job creation, not rate limiter).

### Redis DB allocation

```
Rate limit counters: Redis DB 3
  Key: ratelimit:{key_hash}:{unix_minute}
  TTL: 120s
  Justification: isolation from session (DB 2) and Celery (DB 0/1);
                 rate limit data is throwaway — Redis eviction acceptable
```

---

## Middleware Design

### FastAPI Dependency

```python
async def get_api_key_user(
    api_key: str = Security(APIKeyHeader(name="X-API-Key")),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    # 1. Check Redis cache first (60s TTL)
    cached = await redis.get(f"apikey:{key_hash}")
    if cached:
        user_data = json.loads(cached)
    else:
        # 2. DB lookup
        result = await db.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
        )
        api_key_record = result.scalar_one_or_none()
        if not api_key_record:
            raise HTTPException(401, "Invalid API key")

        user = await db.get(User, api_key_record.user_id)
        user_data = {"user_id": str(user.id), "tier": user.tier}
        await redis.setex(f"apikey:{key_hash}", 60, json.dumps(user_data))

    # 3. Rate limit check (Redis sliding window)
    await check_rate_limit(redis, key_hash, user_data["tier"])

    # 4. Usage event (async, non-blocking — fire-and-forget to Celery)
    record_usage.delay(user_data["user_id"], key_hash)

    return user_data
```

### Unified Auth Dependency

Both JWT (browser/session) and API key flows resolve to the same `User` object:

```python
async def get_current_identity(
    jwt_user: User | None = Depends(get_current_user_optional),
    api_key_user: dict | None = Depends(get_api_key_user_optional),
) -> User:
    if jwt_user:
        return jwt_user
    if api_key_user:
        return api_key_user
    raise HTTPException(401)
```

---

## Key Management API

```
POST /api/keys
  Auth: JWT (browser only — users manage keys in dashboard)
  Body: { name: "CI/CD pipeline", environment: "live" }
  Response: { id, prefix, key: "sdg_live_...", created_at }
             ^^^ plaintext returned ONCE

GET /api/keys
  Auth: JWT
  Response: [{ id, prefix, name, last_used_at, is_active, created_at }]
  Note: key_hash never exposed; prefix shown for identification

DELETE /api/keys/{id}
  Auth: JWT
  → SET is_active = false
  → DEL Redis cache entry "apikey:{key_hash}"
  → Rate limit keys expire naturally (120s TTL)
  Response: 204 No Content
```

---

## Consequences

**Positive:**
- Key hash in DB means a DB breach doesn't expose valid keys
- Redis cache means 95%+ of API requests have zero DB load for auth
- Prefix scheme enables GitHub secret scanning and grep-able log audits
- Sliding window rate limiter is billing-accurate and resilient to burst abuse
- Immediate key revocation via Redis DEL + DB is_active=false

**Negative / Trade-offs:**
- Users cannot recover a lost key (by design — must regenerate)
- Redis DB 3 is best-effort (eviction acceptable); rate limit counters can reset on Redis restart (short-term abuse window — acceptable)
- Fire-and-forget usage recording (Celery) means usage events could be lost on worker crash (mitigated by `task_acks_late = True` from ADR-002)

---

## Dependencies

- `python-multipart` — API key header parsing
- `redis-py` (async) — rate limiting + key cache (already in stack)
- `hashlib` (stdlib) — sha256 key hashing
- `secrets` (stdlib) — entropy source for key generation

---

## Revisit Trigger

Revisit if: API key traffic exceeds 10k req/min sustained — consider Redis Cluster or a dedicated rate-limit proxy (envoy ratelimit). Trigger: P95 rate-limit latency > 5ms.
