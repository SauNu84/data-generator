# Architecture Diagrams — Synthetic Data Generator

- **Date**: 2026-04-01
- **Author**: Enterprise Architect
- **Issue**: SAU-97 (parent: SAU-96)

---

## ERD — Core Data Model

```mermaid
erDiagram
    Org {
        uuid id PK
        string name
        string slug
        string plan "free | paid | enterprise"
        timestamp created_at
        timestamp updated_at
    }

    User {
        uuid id PK
        uuid org_id FK
        string email
        string hashed_password
        string role "owner | member | viewer"
        bool is_active
        timestamp created_at
        timestamp updated_at
    }

    Connector {
        uuid id PK
        uuid org_id FK
        string name
        string connector_type "csv | postgres | mysql | bigquery | snowflake"
        json config "encrypted — connection string, credentials"
        bool is_active
        timestamp created_at
        timestamp updated_at
    }

    Dataset {
        uuid id PK
        uuid org_id FK
        uuid connector_id FK "nullable — null if CSV upload"
        uuid created_by FK
        string name
        string description
        json schema_config "column types, FK relationships, SDV model config"
        string sdv_model "gaussian_copula | ctgan | hma | par"
        string status "draft | ready | archived"
        bigint source_row_count
        timestamp created_at
        timestamp updated_at
    }

    GenerationJob {
        uuid id PK
        uuid dataset_id FK
        uuid org_id FK
        uuid created_by FK
        string status "queued | running | done | failed | cancelled"
        int row_count
        string input_s3_key "nullable — CSV upload path"
        string output_s3_key "nullable — set on completion"
        json quality_score "per-column distribution scores, FK integrity"
        string celery_task_id
        text error_message "nullable"
        timestamp started_at
        timestamp completed_at
        timestamp expires_at "output file TTL"
        timestamp created_at
    }

    Org ||--o{ User : "has"
    Org ||--o{ Connector : "owns"
    Org ||--o{ Dataset : "owns"
    Org ||--o{ GenerationJob : "owns"
    Connector ||--o{ Dataset : "source for"
    Dataset ||--o{ GenerationJob : "generates"
    User ||--o{ Dataset : "created by"
    User ||--o{ GenerationJob : "created by"
```

---

## Component Diagram — Services and Connections

```mermaid
graph TB
    subgraph Client["Client Layer"]
        UI["Web UI\nNext.js 14 + TypeScript\nDataset wizard · Job tracking · Quality viz"]
        CLI["CLI / API Consumers\ncurl · Python SDK · GitHub Actions"]
    end

    subgraph API["API Layer"]
        GW["FastAPI\nuvicorn / gunicorn\n/datasets /connectors /jobs /quality /auth"]
    end

    subgraph Queue["Queue Layer"]
        REDIS["Redis 7\nBroker (DB 0)\nResult backend (DB 1)\nSession cache (DB 2)"]
    end

    subgraph Workers["Worker Layer"]
        W1["Celery Worker 1\nSDV Engine\nGeneration · Quality scoring"]
        W2["Celery Worker N\nHorizontal scale\n(Phase 2+)"]
        FLOWER["Flower\nTask monitoring UI\n:5555"]
    end

    subgraph Connectors["Connector Layer"]
        CC["CSV Reader"]
        PG_CONN["PostgreSQL Connector"]
        BQ_CONN["BigQuery Connector\n(Phase 2)"]
        SF_CONN["Snowflake Connector\n(Phase 3)"]
    end

    subgraph Storage["Storage Layer"]
        MINIO["MinIO (dev)\nor AWS S3 (prod)\nInput files · Generated outputs"]
        POSTGRES["PostgreSQL 15\nMetadata: datasets, jobs,\nusers, orgs, connectors"]
    end

    UI -->|REST + WebSocket| GW
    CLI -->|REST| GW
    GW -->|SQLAlchemy async| POSTGRES
    GW -->|Enqueue task| REDIS
    GW -->|boto3 presigned URL| MINIO
    REDIS -->|Dispatch| W1
    REDIS -->|Dispatch| W2
    W1 -->|Read source data| CC
    W1 -->|Read source data| PG_CONN
    W1 -->|Read source data| BQ_CONN
    W1 -->|Write output| MINIO
    W1 -->|Update job status| POSTGRES
    W1 -->|Task result| REDIS
    W2 -->|Write output| MINIO
    W2 -->|Update job status| POSTGRES
    FLOWER -->|Monitor| REDIS
```

---

## Sequence Diagram — Async Generation Flow

```mermaid
sequenceDiagram
    participant C as Client (UI / API)
    participant API as FastAPI
    participant DB as PostgreSQL
    participant Q as Redis / Celery
    participant W as Celery Worker
    participant SDV as SDV Engine
    participant S3 as MinIO / S3

    C->>API: POST /datasets/{id}/jobs\n{ row_count: 1000 }
    API->>DB: Validate dataset exists + org owns it
    DB-->>API: Dataset record + schema_config

    API->>DB: INSERT GenerationJob\n(status=queued, celery_task_id=pending)
    DB-->>API: job_id

    API->>Q: celery.send_task("generate", kwargs={job_id})\nreturns celery_task_id
    API->>DB: UPDATE GenerationJob SET celery_task_id=...

    API-->>C: 202 Accepted\n{ job_id, status: "queued" }

    Note over C,W: Client polls GET /jobs/{id} or awaits WebSocket push

    Q->>W: Dispatch generate task

    W->>DB: UPDATE GenerationJob status=running, started_at=now()

    alt CSV upload source
        W->>S3: GET input_s3_key → load DataFrame
    else DB connector source
        W->>DB: Fetch Connector config (encrypted)
        W->>W: Decrypt + open DB connection
        W->>W: SELECT * FROM source_table → DataFrame
    end

    W->>SDV: synthesizer = get_synthesizer(schema_config)
    W->>SDV: synthesizer.fit(source_dataframe)
    SDV-->>W: Fitted synthesizer

    W->>SDV: synthetic_df = synthesizer.sample(row_count)
    SDV-->>W: Synthetic DataFrame

    W->>SDV: quality_report = evaluate_quality(source_df, synthetic_df)
    SDV-->>W: Quality scores (per-column distribution match)

    W->>S3: PUT output_s3_key ← synthetic_df.to_csv()
    S3-->>W: ETag / confirmation

    W->>DB: UPDATE GenerationJob\nstatus=done, output_s3_key=...,\nquality_score=..., completed_at=now(),\nexpires_at=now()+24h

    C->>API: GET /jobs/{id}
    API->>DB: SELECT GenerationJob WHERE id=...
    DB-->>API: Job record (status=done)
    API-->>C: 200 OK\n{ status: "done", quality_score: {...} }

    C->>API: GET /jobs/{id}/download
    API->>S3: Generate presigned GET URL (1h expiry)
    S3-->>API: Presigned URL
    API-->>C: 200 OK\n{ download_url: "https://..." }

    C->>S3: GET presigned URL → download synthetic CSV
```

---

## Phase 2 Component Diagram — With Auth, Billing, Rate Limiter, dbt Parser

```mermaid
graph TB
    subgraph Client["Client Layer"]
        UI["Web UI\nNext.js 14 + TypeScript\nAuth · Dashboard · Billing · Multi-table wizard"]
        CLI["CLI / API Consumers\ncurl · Python SDK · CI pipelines"]
    end

    subgraph Auth["Auth Middleware Layer"]
        JWT["JWT Verifier\nHS256 access token\n15-min TTL"]
        APIKEY["API Key Verifier\nSHA-256 lookup\nX-API-Key header"]
        RATE["Rate Limiter\nRedis sliding window\nfree: 60 req/min · pro: 600 req/min"]
        TIER["Tier Enforcer\nFastAPI dependency\nrequire_pro / require_enterprise"]
    end

    subgraph API["API Layer — FastAPI (uvicorn)"]
        AUTH_R["/api/auth/*\nRegister · Login · Refresh\nLogout · Google OAuth · /me"]
        KEYS_R["/api/keys\nCreate · List · Revoke\n(Pro tier only)"]
        BILLING_R["/api/billing/*\nCheckout · Usage\n/api/webhooks/stripe"]
        CORE_R["/api/upload · /api/generate\n/api/jobs/{id} · /api/jobs/{id}/download\n/api/dashboard"]
        DBT_R["/api/dbt/parse\n/api/dbt/generate\nschema.yml → SDV metadata"]
        MULTI_R["/api/datasets/multi\n/api/datasets/{id}/jobs/multi\nHMA multi-table synthesis"]
    end

    subgraph Queue["Queue Layer"]
        REDIS["Redis 7\nBroker (DB 0) · Results (DB 1)\nRate limit counters (DB 2)"]
    end

    subgraph Workers["Worker Layer"]
        W_SINGLE["Celery Worker\nSingle-table synthesis\nGaussianCopula · CTGAN · PAR"]
        W_MULTI["Celery Worker\nMulti-table synthesis\nSDV HMA model"]
        W_JOBS["Celery Beat\nPeriodic: cleanup expired jobs\nRefresh token pruning"]
    end

    subgraph External["External Services"]
        GOOGLE["Google OAuth 2.0\naccounts.google.com"]
        STRIPE["Stripe\nCheckout · Webhooks\nSubscription management"]
    end

    subgraph Storage["Storage Layer"]
        MINIO["MinIO (dev) / S3 (prod)\nInputs: inputs/{uuid}.csv\nOutputs: outputs/{job_id}.csv.zip"]
        POSTGRES["PostgreSQL 15\nusers · refresh_tokens · api_keys\nusage_events · subscriptions\ndatasets · generation_jobs"]
    end

    UI -->|REST + WebSocket| JWT
    CLI -->|X-API-Key header| APIKEY
    JWT --> RATE
    APIKEY --> RATE
    RATE --> TIER
    TIER --> AUTH_R
    TIER --> KEYS_R
    TIER --> BILLING_R
    TIER --> CORE_R
    TIER --> DBT_R
    TIER --> MULTI_R

    AUTH_R -->|bcrypt verify / JWT issue| POSTGRES
    AUTH_R -->|OAuth code exchange| GOOGLE
    KEYS_R -->|SHA-256 store| POSTGRES
    BILLING_R -->|Checkout Session| STRIPE
    STRIPE -->|Webhooks| BILLING_R
    BILLING_R -->|Subscription state update| POSTGRES
    RATE -->|ZADD/ZCARD sliding window| REDIS

    CORE_R -->|SQLAlchemy async| POSTGRES
    CORE_R -->|Enqueue generate task| REDIS
    CORE_R -->|boto3 presigned URL| MINIO
    DBT_R -->|Parse schema.yml → SDV metadata| POSTGRES
    DBT_R -->|Enqueue generate task| REDIS
    MULTI_R -->|FK graph validation| POSTGRES
    MULTI_R -->|Enqueue HMA task| REDIS

    REDIS -->|Dispatch| W_SINGLE
    REDIS -->|Dispatch| W_MULTI
    W_SINGLE -->|SDV fit + sample| MINIO
    W_SINGLE -->|Update job status| POSTGRES
    W_MULTI -->|HMA fit + sample| MINIO
    W_MULTI -->|ZIP multi-table output| MINIO
    W_MULTI -->|Update job status| POSTGRES
    W_JOBS -->|Prune expired tokens/jobs| POSTGRES
    W_JOBS -->|Expire S3 objects| MINIO
```

---

## Phase 2 Auth Flow Sequence

```mermaid
sequenceDiagram
    participant C as Client (SPA / API)
    participant API as FastAPI
    participant G as Google OAuth
    participant DB as PostgreSQL
    participant R as Redis

    Note over C,R: Email/Password Login
    C->>API: POST /api/auth/login { email, password }
    API->>DB: SELECT users WHERE email=... (bcrypt verify)
    DB-->>API: User record
    API->>DB: INSERT refresh_tokens (SHA-256 hash, 7d TTL)
    API-->>C: 200 { access_token (JWT 15m), refresh_token (opaque) }

    Note over C,R: Token Refresh (rotation)
    C->>API: POST /api/auth/refresh { refresh_token }
    API->>DB: SELECT refresh_tokens WHERE hash=... AND revoked=false
    DB-->>API: RefreshToken record
    API->>DB: SET revoked=true (old) + INSERT new RefreshToken
    API-->>C: 200 { new access_token, new refresh_token }

    Note over C,R: Authenticated API Request
    C->>API: POST /api/generate\nAuthorization: Bearer <access_token>
    API->>API: JWT.decode(token) → user_id (no DB hit)
    API->>R: ZADD ratelimit:{user_id} score=now() / ZCARD → count
    alt count > tier limit
        API-->>C: 429 Too Many Requests\nRetry-After: 60
    else within limit
        API->>DB: Validate dataset ownership
        API->>R: Enqueue Celery task
        API-->>C: 202 Accepted { job_id }
    end

    Note over C,R: Google OAuth Flow
    C->>API: GET /api/auth/google
    API-->>C: 302 Redirect → accounts.google.com
    C->>G: User authenticates
    G-->>API: GET /api/auth/google/callback?code=...
    API->>G: POST token exchange → userinfo { sub, email }
    API->>DB: UPSERT User (find by google_sub or email)
    API->>DB: INSERT refresh_tokens
    API-->>C: 302 Redirect /auth/callback#access_token=...&refresh_token=...
```

---

## Phase 2 Stripe Subscription State Machine

```mermaid
stateDiagram-v2
    [*] --> free : User registers
    free --> checkout : POST /api/billing/checkout
    checkout --> trialing : checkout.session.completed\n(trial period configured)
    checkout --> active : checkout.session.completed\n(no trial)
    trialing --> active : trial ends, first payment succeeds
    trialing --> canceled : user cancels during trial
    active --> past_due : payment fails
    past_due --> active : payment retry succeeds
    past_due --> canceled : max retries exceeded
    active --> canceled : user cancels
    canceled --> free : subscription.deleted webhook\n→ users.tier = free
    active --> free : subscription.deleted webhook\n→ users.tier = free (no other active sub)
```

---

## Phase 2 Docker Compose Topology

```mermaid
graph LR
    subgraph docker-compose["Docker Compose (Phase 2 — localhost)"]
        API_SVC["api\nFastAPI:8000\nimage: datagen-api"]
        WORKER_SVC["worker\nCelery (single + multi-table)\nimage: datagen-api"]
        BEAT_SVC["beat\nCelery Beat (cleanup)\nimage: datagen-api"]
        DB_SVC["postgres\nPostgreSQL 15:5432\nimage: postgres:15-alpine"]
        REDIS_SVC["redis\nRedis 7:6379\nBroker + Rate limiter\nimage: redis:7-alpine"]
        MINIO_SVC["minio\nMinIO:9000 (API)\n:9001 (Console)\nimage: minio/minio"]
        FLOWER_SVC["flower\nFlower:5555\nimage: mflower"]
    end

    API_SVC --> DB_SVC
    API_SVC --> REDIS_SVC
    API_SVC --> MINIO_SVC
    WORKER_SVC --> DB_SVC
    WORKER_SVC --> REDIS_SVC
    WORKER_SVC --> MINIO_SVC
    BEAT_SVC --> REDIS_SVC
    BEAT_SVC --> DB_SVC
    FLOWER_SVC --> REDIS_SVC
```

---

## Phase 1 Docker Compose Topology

```mermaid
graph LR
    subgraph docker-compose["Docker Compose (Phase 1 — localhost)"]
        API_SVC["api\nFastAPI:8000\nimage: datagen-api"]
        WORKER_SVC["worker\nCelery\nimage: datagen-api (same image)"]
        DB_SVC["postgres\nPostgreSQL 15:5432\nimage: postgres:15-alpine"]
        REDIS_SVC["redis\nRedis 7:6379\nimage: redis:7-alpine"]
        MINIO_SVC["minio\nMinIO:9000 (API)\n:9001 (Console)\nimage: minio/minio"]
        FLOWER_SVC["flower\nFlower:5555\nimage: mflower"]
    end

    API_SVC --> DB_SVC
    API_SVC --> REDIS_SVC
    API_SVC --> MINIO_SVC
    WORKER_SVC --> DB_SVC
    WORKER_SVC --> REDIS_SVC
    WORKER_SVC --> MINIO_SVC
    FLOWER_SVC --> REDIS_SVC
```
