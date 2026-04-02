# Changelog

All notable changes to the Synthetic Data Generator are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)  
Versioning: [Semantic Versioning](https://semver.org/)

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
