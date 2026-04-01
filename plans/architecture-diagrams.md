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
