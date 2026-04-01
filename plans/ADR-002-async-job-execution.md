# ADR-002: Async Job Execution Framework

- **Status**: Accepted
- **Date**: 2026-04-01
- **Deciders**: Enterprise Architect, CTO
- **Issue**: SAU-97 (parent: SAU-96)

---

## Context

Synthetic data generation is a compute-intensive, long-running operation:
- SDV model fitting on 100k rows can take 30–180 seconds
- Multiple concurrent jobs must not block the API
- Job status must be queryable at any time
- Failed jobs must be retryable without re-uploading data
- Phase 1 is single-node Docker Compose; Phase 2 may horizontally scale workers

The three candidates: **Celery + Redis**, **ARQ (Async Redis Queue)**, **Cloud-native SQS + Lambda**.

---

## Decision Drivers

| Driver | Weight |
|--------|--------|
| Phase 1 simplicity (Docker Compose, minimal infra) | High |
| Retry logic, failure handling, dead-letter queues | High |
| Python-native integration (SDV workers) | High |
| Horizontal worker scaling path (Phase 2) | Medium |
| Operational maturity and documentation | Medium |
| Cloud vendor lock-in risk | Low (Phase 1) |

---

## Options Considered

### Option 1: Celery + Redis

**Pros:**
- Most mature Python task queue; production-proven at scale (Instagram, Robinhood)
- Redis serves dual purpose: Celery broker + result backend + session cache — one fewer infrastructure component
- Rich feature set: task retries, rate limiting, priority queues, task chaining, Canvas (group/chord)
- Flower UI for task monitoring out of the box
- Docker Compose friendly: `redis:7-alpine` + `celery worker` is two lines
- Horizontal scaling: add workers by scaling the worker container
- Direct Python integration — workers import the same models and SDV code as the API

**Cons:**
- Celery has a reputation for configuration complexity at advanced use cases (not applicable at Phase 1 scope)
- Redis broker does not guarantee exactly-once delivery (acceptable for generation jobs with idempotent retries)

**5-year TCO estimate:**
- Zero license cost (MIT + BSD)
- Redis is already in the stack (also used as cache); no additional infra
- Operational cost: 1 Redis instance, N worker containers

### Option 2: ARQ (Async Redis Queue)

**Pros:**
- Lightweight, async-native (asyncio)
- Simpler API than Celery

**Cons:**
- Significantly smaller community and adoption; fewer production case studies
- Missing features needed at Phase 2: priority queues, Canvas (chaining), built-in monitoring
- Less documentation; higher risk of hitting edge cases with no community answers
- Would still use Redis — same infra cost but less feature leverage

**5-year TCO estimate:**
- Higher risk premium: unknown unknowns on scaling path
- Likely migration to Celery at Phase 2 anyway — incurs double migration cost

### Option 3: Cloud-native SQS + Lambda

**Pros:**
- Fully managed, scales to zero, no broker to operate
- Native AWS integration (natural fit for prod S3 path)

**Cons:**
- Lambda has a 15-minute execution limit — SDV training on large datasets can exceed this
- Lambda cold starts add latency to job pickup; not suitable for interactive polling UX
- No local equivalent for Docker Compose dev — mocking SQS/Lambda locally (LocalStack) adds dev friction
- Vendor lock-in: tight coupling to AWS from Phase 1 makes on-prem (Phase 3) significantly harder
- Overkill for Phase 1 single-user MVP; introduces unnecessary infra complexity

**5-year TCO estimate:**
- Higher operational cost once job volume scales (Lambda invocation pricing vs. self-hosted workers)
- High migration cost if on-prem requirement materialises in Phase 3

---

## Decision

**Celery + Redis (Phase 1 and 2)**

Celery + Redis is the correct choice because:
1. **Phase 1 simplicity** — one Redis container, one worker container; Docker Compose runnable in minutes.
2. **No execution time limit** — Lambda's 15-minute cap is a hard blocker for complex SDV jobs.
3. **Local dev parity** — no LocalStack mocking; identical queue behavior dev-to-prod.
4. **Feature growth path** — priority queues, chaining, Canvas are available when needed (Phase 2+) without migration.
5. **Redis dual-use** — broker + cache in one container reduces Phase 1 infra footprint.

---

## Consequences

**Positive:**
- API and workers share Python models and SDV code directly
- Retry-on-failure with exponential backoff is one decorator line
- Flower dashboard provides ops visibility from day one
- Horizontal worker scaling requires only a container count change

**Negative / Trade-offs:**
- Not serverless; workers are always-on (acceptable cost at Phase 1 scale)
- Redis must be treated as critical infra; add persistence (`appendonly yes`) for job durability

**Configuration standards:**
```python
# celery config (celeryconfig.py)
broker_url = "redis://redis:6379/0"
result_backend = "redis://redis:6379/1"
task_serializer = "json"
result_expires = 86400           # 24h — matches S3 TTL on generated files
task_acks_late = True            # Re-queue on worker crash
worker_prefetch_multiplier = 1   # Fair dispatch for long-running tasks
task_time_limit = 1800           # 30-min hard kill (Phase 1 safety)
task_soft_time_limit = 1500      # 25-min graceful stop
```

---

## Revisit Trigger

Revisit if: Phase 3 requires serverless on-demand burst scaling (SQS + Fargate or Lambda with Provisioned Concurrency could replace Celery workers). Trigger point: sustained queue depth >1000 jobs/hour.
