# ADR-003: File Storage — MinIO (Dev) vs Direct S3

- **Status**: Accepted
- **Date**: 2026-04-01
- **Deciders**: Enterprise Architect, CTO
- **Issue**: SAU-97 (parent: SAU-96)

---

## Context

The synthetic data generator must store:
1. **Input files** — uploaded CSVs (or DB connector snapshots) used as training data
2. **Generated output files** — synthetic CSV/Parquet files ready for download
3. **Metadata** — stored in PostgreSQL, not in object storage

Files are ephemeral: inputs expire after job completion, outputs expire after 24h (free tier) or 7 days (paid tier). The system must work identically in local Docker Compose development and AWS production.

Candidates: **MinIO (dev) proxying to S3 (prod)** vs **direct S3 everywhere**.

---

## Decision Drivers

| Driver | Weight |
|--------|--------|
| Dev/prod parity (no behaviour differences local vs. prod) | High |
| Local development without AWS credentials | High |
| S3-compatible API (no code changes at promotion) | High |
| Operational simplicity | Medium |
| Cost (dev environment) | Medium |
| Vendor lock-in risk | Low |

---

## Options Considered

### Option 1: MinIO (dev) + S3 (prod) — same boto3/S3-compatible client

**Architecture:**
```
Local (Docker Compose):    MinIO container  →  localhost:9000
Production (AWS):          AWS S3           →  s3.amazonaws.com
Code:                      boto3 with configurable endpoint_url
```

**Pros:**
- **Full dev/prod parity** — MinIO implements the S3 API; the same `boto3` code works against both. No LocalStack, no mocks, no conditional branches.
- **No AWS credentials in local dev** — developers use `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`; AWS credentials are only in prod CI/CD secrets.
- **MinIO is production-grade** — used by Nasdaq, Verizon, and others; not a toy. Phase 1 could even deploy MinIO to prod if cost is a concern.
- **Docker Compose trivial** — `minio/minio:latest` in one service block.
- **MinIO Console** — built-in browser UI for object inspection during development.

**Cons:**
- One extra container in Docker Compose (minor)
- Must maintain `endpoint_url` config toggle between environments

**Implementation:**
```python
import boto3
import os

s3 = boto3.client(
    "s3",
    endpoint_url=os.getenv("S3_ENDPOINT_URL"),  # None in prod = AWS default
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)
# BUCKET_NAME env var: "datagen-files" (MinIO dev) or "your-prod-bucket" (S3)
```

### Option 2: Direct S3 Everywhere (including local dev)

**Pros:**
- No MinIO container to manage
- Uses real S3 from day one

**Cons:**
- **Requires AWS credentials in local dev** — adds friction for new contributors (account creation, IAM setup, policy config)
- **Costs money during development** — every PUT/GET during dev testing incurs S3 charges
- **Slower inner loop** — network latency to AWS vs. local container
- **Cannot work offline** — developer on a plane cannot run the app
- **Not reproducible** — dev environment depends on external infrastructure state

---

## Decision

**MinIO for development, AWS S3 for production — single boto3 codebase with configurable `endpoint_url`.**

This is a **dev/prod parity** decision, not a storage technology decision. The application code is identical in both environments. MinIO's S3 API compatibility means zero risk of divergence. The single configuration toggle is `S3_ENDPOINT_URL` (set to MinIO URL locally, absent in production).

---

## Consequences

**Positive:**
- New developer is running the full stack locally in `docker compose up` with no AWS account
- Zero cost local dev (no S3 PUT/GET charges)
- Identical object lifecycle behavior (TTL via scheduled Celery task, same in both environments)
- Path to MinIO-on-prem production for Phase 3 enterprise customers — no code changes needed

**Negative / Trade-offs:**
- Docker Compose adds one service (MinIO + its data volume)
- Must keep `S3_ENDPOINT_URL` in environment variable management; document clearly

**Environment variable contract:**
```
# .env.dev
S3_ENDPOINT_URL=http://minio:9000
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
S3_BUCKET_NAME=datagen-files

# .env.prod (S3_ENDPOINT_URL absent = boto3 uses AWS default)
AWS_ACCESS_KEY_ID=<iam-role-via-ec2-instance-profile>
AWS_SECRET_ACCESS_KEY=<iam-role-via-ec2-instance-profile>
S3_BUCKET_NAME=<prod-bucket-name>
```

**Bucket policy (prod S3):**
- Private ACL; no public access
- Lifecycle rule: expire objects with prefix `outputs/` after 24h (free) or 7 days (paid)
- Signed URLs (presigned GET, 1h expiry) for all download endpoints

---

## Revisit Trigger

Revisit if: Phase 3 on-prem requirement needs on-prem object storage at scale → MinIO cluster (distributed mode) is the path; no application code change required.
