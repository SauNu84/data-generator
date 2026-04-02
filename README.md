# Synthetic Data Generator

Free, developer-first CSV → synthetic CSV generator. Upload a real dataset, review the inferred schema, pick a model, and download a privacy-safe synthetic copy — no login required.

---

## Screenshots

### 1. Upload
Drop any CSV (≤ 50 MB, 100k rows) directly onto the page — no account needed.

![Upload page](docs/screenshots/01-upload.png)

### 2. Schema Review
Auto-inferred column types (numerical, categorical, datetime, ID). Override any column before generating.

![Schema review](docs/screenshots/02-schema-review.png)

### 3. Results & Quality Score
Async results page with live polling. Overall quality score + per-column breakdown. Shareable URL, 24-hour download TTL.

![Results page](docs/screenshots/03-results.png)

### 4. Architecture
7-service Docker Compose stack — single command to run everything locally.

![Architecture diagram](docs/screenshots/04-architecture.png)

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/SauNu84/data-generator.git
cd data-generator

# 2. Start all services
docker compose up --build

# 3. Open the app
open http://localhost:3000
```

That's it. No environment variables, no accounts, no config required for local dev.

**Services started by Docker Compose:**

| Service    | Port | Description                         |
|------------|------|-------------------------------------|
| frontend   | 3000 | Next.js UI                          |
| api        | 8000 | FastAPI REST backend                |
| worker     | —    | Celery generation worker (SDV)      |
| postgres   | 5432 | Job + dataset metadata              |
| redis      | 6379 | Job queue + result backend          |
| minio      | 9000 | S3-compatible file storage          |
| minio-init | —    | One-shot bucket initialiser         |

---

## How It Works

```
Upload CSV (≤50MB)
      ↓
Schema inference (pandas dtypes → detected_type)
      ↓
User reviews + overrides column types
      ↓
Celery job enqueued → SDV fits the model
      ↓
Quality score computed (statistical fidelity)
      ↓
Synthetic CSV stored in MinIO (24h TTL)
      ↓
User downloads via presigned URL
```

### Models

| Model | Speed | Best For |
|-------|-------|----------|
| **GaussianCopula** (default) | Fast (~58s @ 100k rows) | Numeric-heavy datasets |
| **CTGAN** | Slower | Categorical-heavy datasets |

---

## API Reference

The REST API is fully functional for programmatic use — no UI required.

```bash
# 1. Upload CSV
curl -X POST http://localhost:8000/api/upload \
  -F "file=@your_data.csv"
# → { "dataset_id": "...", "columns": [...], "row_count": N }

# 2. Enqueue generation
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"dataset_id": "...", "row_count": 1000, "model": "GaussianCopula"}'
# → { "job_id": "..." }

# 3. Poll status
curl http://localhost:8000/api/jobs/{job_id}
# → { "status": "done", "quality_score": 0.90, "download_url": "..." }

# 4. Download
curl -L "{download_url}" -o synthetic.csv
```

Full OpenAPI docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Development

### Prerequisites
- Docker Desktop ≥ 4.x
- (Optional for frontend dev) Node.js 20+

### Backend only (no Docker)

```bash
pip install -r requirements.txt

# Requires local Postgres, Redis, MinIO — use docker compose for those:
docker compose up postgres redis minio minio-init -d

cp .env.dev .env
alembic upgrade head
uvicorn app.main:app --reload
```

### Frontend only

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

### Run tests

```bash
# Backend
pytest

# Frontend
cd frontend && npm test
```

### CI

GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push to `main` and on all pull requests:
- Backend: `pytest` with coverage
- Frontend: `jest` unit tests + `next build` (type-check + lint)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15, TypeScript, Tailwind CSS |
| Backend | FastAPI, SQLAlchemy (async), Alembic |
| Generation | SDV 1.35.1 (GaussianCopula, CTGAN) |
| Queue | Celery 5 + Redis |
| Storage | MinIO (S3-compatible) |
| Database | PostgreSQL 16 |
| Infra | Docker Compose, GitHub Actions CI |

---

## Project Structure

```
data-generator/
├── app/                   # FastAPI backend
│   ├── main.py            # Routes: /api/upload, /api/generate, /api/jobs
│   ├── tasks.py           # Celery worker: SDV fit + quality scoring
│   ├── models.py          # SQLAlchemy: Dataset, GenerationJob
│   ├── schemas.py         # Pydantic request/response models
│   ├── storage.py         # MinIO helpers (upload, presigned URL)
│   └── config.py          # Settings (env-based)
├── frontend/              # Next.js application
│   ├── app/page.tsx       # Upload + schema review page
│   ├── app/jobs/[job_id]/ # Results + quality score page
│   ├── components/        # DropZone, SchemaTable
│   └── lib/               # api.ts, analytics.ts
├── alembic/               # Database migrations
├── tests/                 # Pytest test suite
├── docs/screenshots/      # UI screenshots
├── docker-compose.yml     # Full-stack local dev
├── Dockerfile             # API + worker image
└── CHANGELOG.md           # Version history
```

---

## Roadmap

See [CHANGELOG.md](CHANGELOG.md) for what shipped in Phase 1.

**Phase 2 (planned):**
- User auth + saved dataset history
- dbt schema.yml integration
- Sample dataset templates (e-commerce, HR, fintech)
- AI Agent mode — describe your data in natural language
- Multi-table synthesis (foreign key relationships)

---

## License

MIT
