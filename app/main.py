"""
FastAPI application — Phase 1 Synthetic Data Generator.

Endpoints:
  POST /api/upload                — upload CSV, store in MinIO/S3, infer schema
  POST /api/generate              — enqueue SDV generation Celery job
  GET  /api/jobs/{job_id}         — poll job status + quality score
  GET  /api/jobs/{job_id}/download — presigned download URL
  GET  /health                    — liveness probe
"""

import io
import uuid
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, engine, get_db
from app.deps import get_current_user_or_api_key
from app.models import Base, Dataset, GenerationJob, User
from app.routes import auth as auth_router
from app.routes import billing as billing_router
from app.routes import dashboard as dashboard_router
from app.routes import database as database_router
from app.routes import dbt as dbt_router
from app.routes import keys as keys_router
from app.routes import multi_table as multi_table_router
from app.routes import samples as samples_router
from app.pii import scan_dataframe
from app.schemas import (
    ColumnQuality,
    ColumnSchema,
    DownloadResponse,
    GenerateRequest,
    GenerateResponse,
    JobStatusResponse,
    PiiColumnInfo,
    UploadResponse,
)
from app.storage import ensure_bucket, generate_presigned_url, upload_csv_bytes
from app.tasks import generate_synthetic_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables (idempotent; Alembic handles migrations in prod)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Ensure MinIO bucket exists
    ensure_bucket()
    yield


app = FastAPI(
    title="Synthetic Data Generator API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ──────────────────────────────────────────────────────────────────

app.include_router(auth_router.router)
app.include_router(keys_router.router)
app.include_router(billing_router.router)
app.include_router(dashboard_router.router)
app.include_router(dbt_router.router)
app.include_router(samples_router.router)
app.include_router(multi_table_router.router)
app.include_router(database_router.router)


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ─── Upload ───────────────────────────────────────────────────────────────────

def _infer_schema(df: pd.DataFrame) -> list[ColumnSchema]:
    from sdv.metadata import Metadata

    meta = Metadata.detect_from_dataframe(df)
    col_meta = meta.tables[""] if "" in (meta.tables or {}) else {}
    # Fallback: detect column sdtypes from metadata object structure
    columns_meta: dict = {}
    try:
        # SDV 1.x: access via metadata.columns or metadata.to_dict()
        meta_dict = meta.to_dict()
        # structure: {"tables": {"": {"columns": {...}}}} or flat {"columns": {...}}
        tables = meta_dict.get("tables") or {}
        for tbl in tables.values():
            columns_meta = tbl.get("columns", {})
            break
        if not columns_meta:
            columns_meta = meta_dict.get("columns", {})
    except Exception:
        columns_meta = {}

    result = []
    for col in df.columns:
        sdtype = columns_meta.get(col, {}).get("sdtype", "categorical")
        result.append(
            ColumnSchema(name=col, sdtype=sdtype, dtype=str(df[col].dtype))
        )
    return result


@app.post("/api/upload", response_model=UploadResponse, status_code=201)
async def upload_csv(
    file: UploadFile = File(..., description="CSV file to use as training data"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    # Size guard (reads into memory — fine for ≤50MB)
    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum {settings.max_upload_bytes // (1024*1024)} MB.",
        )

    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse CSV: {exc}") from exc

    if df.empty:
        raise HTTPException(status_code=400, detail="Uploaded CSV is empty.")

    if len(df) > settings.max_upload_rows:
        raise HTTPException(
            status_code=422,
            detail=f"CSV exceeds {settings.max_upload_rows:,} row hard cap ({len(df):,} rows).",
        )

    # Upload raw CSV to storage
    s3_key = upload_csv_bytes(raw, prefix="inputs")

    # Infer schema
    schema = _infer_schema(df)

    # Scan for PII
    pii_result = scan_dataframe(df)
    pii_info = [
        PiiColumnInfo(column=c.column, pii_type=c.pii_type, detection_method=c.detection_method)
        for c in pii_result.pii_columns
    ]

    # Persist Dataset record — schema_json stores columns + pii metadata
    dataset = Dataset(
        original_filename=file.filename or "upload.csv",
        s3_key=s3_key,
        row_count=len(df),
        schema_json={
            "columns": [c.model_dump() for c in schema],
            "pii_columns": [p.model_dump() for p in pii_info],
            "mode": "single_table",
        },
        user_id=current_user.id,
    )
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)

    return UploadResponse(
        dataset_id=dataset.id,
        original_filename=dataset.original_filename,
        row_count=dataset.row_count,
        columns=schema,
        pii_columns=pii_info,
    )


# ─── Generate ─────────────────────────────────────────────────────────────────

@app.post("/api/generate", response_model=GenerateResponse, status_code=202)
async def start_generation(
    req: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    from datetime import datetime, timezone
    from calendar import monthrange
    from sqlalchemy import func
    from app.models import UsageEvent

    # Free tier: check monthly generation limit
    if current_user.tier == "free":
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = await db.scalar(
            select(func.count(UsageEvent.id)).where(
                UsageEvent.user_id == current_user.id,
                UsageEvent.event_type == "generation",
                UsageEvent.created_at >= month_start,
            )
        )
        if (used or 0) >= settings.free_tier_monthly_generations:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Free tier limit reached ({settings.free_tier_monthly_generations} "
                    "generations/month). Upgrade to Pro for unlimited access."
                ),
            )

    # Verify dataset exists and belongs to this user
    dataset = await db.get(Dataset, req.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if dataset.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied.")

    # Create job record
    job = GenerationJob(
        dataset_id=req.dataset_id,
        status="queued",
        model_type=req.model_type,
        requested_rows=req.num_rows,
    )
    db.add(job)

    # Record usage event
    db.add(UsageEvent(user_id=current_user.id, event_type="generation"))

    await db.commit()
    await db.refresh(job)

    # Enqueue Celery task
    generate_synthetic_data.delay(
        str(job.id),
        str(req.dataset_id),
        req.model_type,
        req.num_rows,
        req.schema_overrides,
    )

    return GenerateResponse(
        job_id=job.id,
        dataset_id=req.dataset_id,
        status=job.status,
        model_type=job.model_type,
    )


# ─── Job Status ───────────────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    job = await db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    # Verify ownership via dataset
    dataset = await db.get(Dataset, job.dataset_id)
    if not dataset or dataset.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied.")

    quality_score: float | None = None
    column_quality: list[ColumnQuality] | None = None
    if job.quality_score_json:
        q = job.quality_score_json
        quality_score = q.get("overall")
        cols = q.get("columns", [])
        column_quality = [ColumnQuality(**c) for c in cols] if cols else None

    download_url: str | None = None
    if job.status == "done" and job.output_s3_key:
        try:
            download_url = generate_presigned_url(job.output_s3_key)
        except Exception:
            pass  # non-fatal; client can call /download endpoint directly

    return JobStatusResponse(
        job_id=job.id,
        dataset_id=job.dataset_id,
        status=job.status,
        model_type=job.model_type,
        requested_rows=job.requested_rows,
        quality_score=quality_score,
        column_quality=column_quality,
        error=job.error_detail,
        download_url=download_url,
        expires_at=job.expires_at,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


# ─── Download ─────────────────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/download", response_model=DownloadResponse)
async def get_download_url(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_or_api_key),
):
    job = await db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    dataset = await db.get(Dataset, job.dataset_id)
    if not dataset or dataset.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied.")

    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Job is not done (status: {job.status}).")

    if not job.output_s3_key:
        raise HTTPException(status_code=410, detail="Output file has expired or was removed.")

    url = generate_presigned_url(job.output_s3_key)

    return DownloadResponse(
        job_id=job.id,
        url=url,
        expires_in_seconds=settings.s3_presigned_url_expiry,
    )
