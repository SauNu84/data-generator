# ADR-001: API Layer Framework Selection

- **Status**: Accepted
- **Date**: 2026-04-01
- **Deciders**: Enterprise Architect, CTO
- **Issue**: SAU-97 (parent: SAU-96)

---

## Context

The commercial synthetic data generator requires a REST API layer that:
- Accepts dataset uploads and configuration
- Dispatches async generation jobs
- Serves job status and signed download URLs
- Supports WebSocket push for live job progress (Phase 2)
- Is built and maintained by a Python-native team already using SDV

The three primary candidates evaluated: **FastAPI**, **Django (+ DRF)**, **Node.js (Express/NestJS)**.

---

## Decision Drivers

| Driver | Weight |
|--------|--------|
| Python ecosystem compatibility (SDV, Celery, Pandas) | High |
| Async-first design (job dispatch, WebSocket) | High |
| Developer velocity (schema-driven, auto-docs) | High |
| Production maturity and operational track record | Medium |
| Community and hiring pool | Medium |
| 5-year TCO | Medium |

---

## Options Considered

### Option 1: FastAPI (Python)

**Pros:**
- Native async (ASGI + asyncio) — matches Celery task dispatch patterns
- Automatic OpenAPI/Swagger docs from type hints (zero extra work)
- Pydantic v2 for request/response validation — type-safe, fast
- Same language as SDV, Celery workers, data processing — single stack
- Strong adoption: Uber, Netflix, Microsoft; GitHub stars >75k
- uvicorn/gunicorn production deployment is well-documented

**Cons:**
- Younger ecosystem than Django (fewer built-in batteries)
- Admin interface requires third-party add-on (not needed Phase 1)

**5-year TCO estimate:**
- Zero license cost (MIT)
- Low ops overhead — single language, lightweight ASGI server
- Smaller team onboarding cost vs. context-switching to Node.js

### Option 2: Django + Django REST Framework

**Pros:**
- Battle-tested, rich ecosystem (ORM, migrations, admin, auth)
- Large hiring pool

**Cons:**
- Synchronous-first (WSGI default); async support is bolted on, not native
- Heavyweight for a microservice API — admin, ORM, templates mostly unused
- DRF serializers are verbose vs. Pydantic; slower iteration
- Adds cognitive overhead when workers are FastAPI/Celery/Python anyway

**5-year TCO estimate:**
- Higher dev time per endpoint vs. FastAPI (DRF verbosity)
- Unnecessary framework weight increases maintenance surface

### Option 3: Node.js (Express/NestJS)

**Pros:**
- Strong async model (event loop)
- Large ecosystem

**Cons:**
- Language mismatch: SDV, Celery workers, and data processing are all Python
- Dual-language stack splits hiring, adds cross-language serialization complexity
- NestJS adds architectural overhead that is overkill for Phase 1 scope
- No direct SDV integration path — requires subprocess calls or separate service boundary

**5-year TCO estimate:**
- Highest TCO: two language runtimes, two dependency graphs, bridge layer for SDV calls

---

## Decision

**FastAPI (Python 3.11+)**

FastAPI is the correct choice because:
1. **Python ecosystem fit** — SDV, Celery, Pandas, and all data processing are Python. A single-language stack reduces cognitive overhead, simplifies CI/CD, and lowers hiring bar.
2. **Async-native** — ASGI + asyncio aligns with Celery task dispatch and the future WebSocket push requirement.
3. **Auto-docs** — Pydantic + OpenAPI docs are generated without extra work, accelerating frontend integration.
4. **Production-proven** — not an experiment; well-adopted at scale with documented operational patterns.

---

## Consequences

**Positive:**
- Single Python runtime across API, workers, and data layer
- Type-safe request/response contracts via Pydantic — fewer runtime bugs
- Auto-generated OpenAPI spec unblocks front-end development immediately
- Celery integration is first-class (shared Pydantic models, common DB session)

**Negative / Trade-offs:**
- No built-in admin UI (not needed Phase 1; Adminer/pgAdmin for DB ops)
- Must manage migrations separately (Alembic + SQLAlchemy, not Django ORM)

**Risks:**
- None material at Phase 1 scope

---

## Implementation Notes

```
Tech stack:
  Runtime:     Python 3.11
  Framework:   FastAPI 0.110+
  Server:      uvicorn (dev), gunicorn + uvicorn workers (prod)
  Validation:  Pydantic v2
  ORM:         SQLAlchemy 2.x (async)
  Migrations:  Alembic
  Auth:        python-jose (JWT, Phase 2)
```

---

## Revisit Trigger

Revisit if: team expands to >5 engineers who are Node.js-primary, OR if a micro-frontend BFF pattern emerges requiring a Node.js gateway. Neither is expected before Phase 3.
