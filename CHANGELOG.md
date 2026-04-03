# Changelog

All notable changes to the Synthetic Data Generator are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)  
Versioning: [Semantic Versioning](https://semver.org/)

---

## [0.2.0] — 2026-04-03 — Phase 2: Commercial Platform

### Added

#### M1 — Monetisation Foundation (SAU-107)

**Auth & Accounts**
- User registration + email/password login with bcrypt (cost 12)
- Google OAuth 2.0 flow (`/auth/google` → `/auth/google/callback`)
- JWT access tokens (15 min) + rotating refresh tokens (7 day, SHA-256 hashed in Postgres)
- `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`
- Alembic migration `002`: `users`, `refresh_tokens`, `api_keys`, `usage_events`, `subscriptions` tables + `datasets.user_id` FK

**API Key Management** (Pro tier)
- `sdg_live_<32-hex>` format keys; only prefix shown after creation, raw key never stored
- `POST /api/keys`, `GET /api/keys`, `DELETE /api/keys/{id}` — create, list, revoke
- `X-API-Key` header auth path (updates `last_used_at` + `request_count` on each use)

**Stripe Billing**
- `POST /api/billing/checkout` — Stripe Checkout session (Pro subscription)
- `GET /api/billing/usage` — current tier, monthly generation count, limit
- `POST /api/webhooks/stripe` — handles `customer.subscription.*` lifecycle events; syncs user tier
- Free tier: 10 generations/month enforced at `/api/generate` via DB count (no external rate-limit dependency)
- `UsageEvent` recorded on each generation for billing audit trail

**User Dashboard API + Frontend**
- `GET /api/dashboard` — paginated dataset list with per-dataset job counts
- `DELETE /api/dashboard/{id}` — soft-delete dataset (owner-checked)
- Frontend: `/login`, `/register`, `/dashboard`, `/auth/callback` pages
- Dashboard: usage banner, free-tier limit warning, upgrade-to-Pro prompt, dataset table with pagination, one-click re-generate and delete

#### M2 — ICP Features (SAU-108)

**dbt Integration** (Pro tier)
- `POST /api/dbt/parse` — accepts `schema.yml` YAML; maps dbt `data_type` → SDV `sdtype`; extracts constraints, handles UUID/datetime/boolean edge cases
- `POST /api/dbt/generate` — parse + enqueue SDV job directly from dbt schema; no CSV upload required

**PII Auto-Detection + Masking**
- `app/pii.py`: regex + name-heuristic detection for email, phone, SSN, credit card, full name, street address, IP address
- PII scan wired into `POST /api/upload` — `pii_columns` returned in `UploadResponse`
- PII-flagged columns are masked with Faker-generated values before SDV fit (no real PII enters the model)

**Sample Dataset Templates**
- `GET /api/samples` — lists 4 built-in templates: e-commerce (orders/products/customers), HR (employees), fintech (transactions), healthcare (patients)
- `POST /api/samples/{id}/load` — loads template as a ready-to-generate Dataset (skips CSV upload)

#### M3 — Enterprise Path (SAU-108)

**Multi-table Synthesis** (Enterprise tier)
- `POST /api/upload/multi-table` — accepts ZIP of CSVs + `relationships` JSON (FK graph)
- `POST /api/multi-table/{dataset_id}/generate` — enqueues HMA (Hierarchical Modeling Algorithm) job
- Celery task: fits `HMASynthesizer`, samples with `scale_factor`, per-table quality scores, outputs ZIP of synthetic CSVs

**Database Connector** (Enterprise tier)
- `POST /api/connect/database` — accepts connection string + table name; lists tables via SQLAlchemy inspector (no schema stored)
- `POST /api/connect/database/load` — samples rows from target table → `Dataset`; connection strings never persisted; table names validated against inspector whitelist (SQL injection prevention)
- Supports PostgreSQL and MySQL

#### Architecture & Planning (SAU-106)
- ADR-004: Auth & Account System — JWT + rotating refresh tokens, Google OAuth, tier-gated deps
- ADR-005: API Key Management — `sdg_live_` prefix format, SHA-256 hash storage
- ADR-006: Stripe Billing — Checkout flow, webhook idempotency, tier sync pattern
- ADR-007: dbt Integration — schema.yml parsing strategy, Pro tier gate
- ADR-008: Multi-table + DB Connector — HMA model selection, Enterprise gate, zero-persistence connector policy
- Updated ERD and component architecture diagrams

#### Test Coverage (SAU-109 – SAU-118)
- Phase 1 backend coverage: 68% → raised critical gaps to 100% across `app/tasks.py`, `app/storage.py`, `app/routes/auth.py`, `app/routes/billing.py`
- Frontend `lib/api.ts`: 17% → 100%
- Phase 2 frontend pages (login, register, dashboard): 0% → 100%
- E2E auth scaffolding: Playwright page-object tests for register/login/logout/dashboard flows
- CI: `e2e-test` job added to GitHub Actions pipeline

### Fixed
- **CI: MinIO service container crash** — replaced `minio/minio` + broken `--entrypoint "/bin/sh -c '...'"` with `bitnami/minio:latest` (auto-starts; entrypoint override not needed). Affected `backend-test` and `e2e-test` jobs.
- **CI: Next.js prerender error on `/dashboard`** — `useSearchParams()` without Suspense boundary caused static render failure. Fixed with `export const dynamic = "force-dynamic"` on the dashboard page.

### Changed
- `POST /api/generate` now requires auth (free tier: 10/month; Pro: unlimited); unauthenticated requests receive `401`
- `POST /api/upload` now returns `pii_columns` array in response
- Docker Compose: MinIO health-check updated to use `bitnami/minio` compatible path

### Technical Decisions
| Decision | Choice | Rationale |
|----------|--------|-----------|
| Auth token storage | JWT (15 min) + DB refresh tokens | Redis-primary revoked in favour of Postgres for audit trail durability |
| API key format | `sdg_live_<32 hex>` + SHA-256 hash | Never store plaintext; prefix sufficient for display |
| PII masking | Faker replacement before SDV fit | PII never enters model; masked output still statistically realistic |
| Multi-table model | SDV HMASynthesizer | Native FK relationship support; only model handling relational structure |
| DB connector persistence | Zero (connection string discarded after load) | Security-first; no credentials at rest |

---

## [0.1.0] — 2026-04-02 — Phase 1 MVP

### Added

#### Backend (SAU-99)
- **FastAPI** REST API with 5 endpoints:
  - `POST /api/upload` — CSV ingest (50 MB / 100k row hard cap), schema inference via SDV Metadata
  - `POST /api/generate` — enqueue SDV generation job (GaussianCopula or CTGAN)
  - `GET /api/jobs/{job_id}` — poll job status with quality score on completion
  - `GET /api/jobs/{job_id}/download` — presigned S3/MinIO download URL (24 h TTL)
  - `GET /health` — liveness probe
- **Celery + Redis** async worker pipeline for SDV generation
- **SDV 1.35.1** integration: GaussianCopula (default) and CTGAN model support
- **Quality scoring**: overall percentage + per-column similarity breakdown
- **MinIO** (dev) / S3 (prod) storage with presigned upload/download URLs
- **PostgreSQL** data models: `Dataset` and `GenerationJob` via SQLAlchemy async ORM
- **Alembic** migration: `001_initial_schema`
- **24 h TTL cleanup** Celery beat task removes expired output files

#### Frontend (SAU-100)
- **Next.js 14** app (App Router, TypeScript, Tailwind CSS)
- Upload page (`/`) with drag-and-drop CSV zone, client-side 50 MB validation
- Schema review table with per-column type override dropdowns
- Row count input (default: source size, max 100 k) and model toggle (GaussianCopula / CTGAN)
- Async results page (`/jobs/[job_id]`) with 3 s polling
- Quality score: overall % prominently displayed + per-column bar chart
- "Download CSV" button linked to presigned URL
- Full error states: file too large, too many rows, upload failure, generation failure, download expired
- Analytics stub (`window.analytics.track`) firing on upload, generate, results view, download
- Jest + React Testing Library component tests (4 test files, 35 + cases)

#### Infrastructure (SAU-101)
- **Docker Compose** full-stack environment: `postgres`, `redis`, `minio`, `minio-init`, `api`, `worker`, `frontend` (7 services)
- Single `docker compose up` starts the entire stack with health-check ordering
- **GitHub Actions CI** (`.github/workflows/ci.yml`):
  - `backend-test`: pytest in Docker with coverage report
  - `frontend-test`: Jest + `next build` type check
  - `docker-build`: verifies all images build cleanly
- `.env.example` documenting all required environment variables

#### API Contract Fix (SAU-103 / SAU-104)
- Resolved 5 frontend/backend field mismatches discovered in QA:
  - `dataset_id` (was `datasetId`) on upload response
  - `detected_type` → `sdtype` column schema field alignment
  - `num_rows` (was `numRows`) on generate request
  - `quality_score` float field added to `JobStatusResponse`
  - `column_quality` array field added to `JobStatusResponse`
- Fixed 2 P1 bugs:
  - CORS middleware wildcard configuration for local dev
  - Presigned URL generation exception swallowed silently — now surfaced in job status

#### Architecture & Planning
- ADR-001: API layer framework selection (FastAPI chosen over Django, Node.js)
- ADR-002: Async job execution design (Celery + Redis)
- ADR-003: File storage strategy (MinIO dev / S3 prod parity)
- System architecture diagrams (ERD, component map, sequence flows)

### Technical Decisions
| Decision | Choice | Rationale |
|----------|--------|-----------|
| API framework | FastAPI | Python-native, async-first, auto-docs, SDV ecosystem fit |
| Job queue | Celery + Redis | Proven, simple, matches Python stack |
| Storage | MinIO (dev) / S3 (prod) | S3-compatible parity, zero prod code changes |
| Generation engine | SDV 1.35.1 | GaussianCopula: ~58 s / 100 k rows, ~84% quality score |
| Frontend | Next.js 14 | App Router, TypeScript, Tailwind |
| Database | PostgreSQL 16 | Reliable, async ORM support |

### Performance Baselines (from SAU-98 spike)
| Rows | GaussianCopula | Quality Score |
|-----:|:--------------:|:-------------:|
| 5,000 | ~2 s | ~87% |
| 50,000 | ~18 s | ~85% |
| 100,000 | ~58 s | ~84% |

---

## [Unreleased]

### Planned — Phase 2
- AI Agent mode: natural language data description → schema generation
- Sample dataset templates (generic domain: e-commerce, HR, finance)
- dbt seed file export
- Database connector (PostgreSQL, MySQL)
- Auth / accounts
- Email notifications on job completion
- Multi-table generation
- WebSocket live job progress
