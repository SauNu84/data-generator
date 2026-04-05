"""
dbt schema.yml integration routes:
  POST /api/dbt/parse     — parse dbt schema.yml, return SDV metadata preview
  POST /api/dbt/generate  — parse + create Dataset + enqueue generation job
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user_or_api_key
from app.dbt_parser import parse_dbt_schema, to_sdv_metadata
from app.models import Dataset, GenerationJob, UsageEvent
from app.schemas import (
    DbtGenerateRequest,
    DbtGenerateResponse,
    DbtModelPreview,
    DbtParseRequest,
    DbtParseResponse,
)
from app.tasks import generate_synthetic_data

router = APIRouter(prefix="/api/dbt", tags=["dbt"])


def _require_pro(user) -> None:
    if user.tier not in ("pro", "enterprise"):
        raise HTTPException(
            status_code=402,
            detail="dbt integration requires a Pro or Enterprise plan.",
        )


# ─── Parse ────────────────────────────────────────────────────────────────────

@router.post("/parse", response_model=DbtParseResponse)
async def parse_dbt(
    req: DbtParseRequest,
    current_user=Depends(get_current_user_or_api_key),
):
    """Parse a dbt schema.yml and return SDV metadata preview per model."""
    _require_pro(current_user)

    try:
        models = parse_dbt_schema(req.schema_yaml)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    previews = []
    for model in models:
        if not model.columns:
            model.warnings.append(f"Model '{model.name}' has no columns — skipped.")
            continue
        previews.append(
            DbtModelPreview(
                name=model.name,
                column_count=len(model.columns),
                sdv_metadata=to_sdv_metadata(model),
                warnings=model.warnings,
            )
        )

    return DbtParseResponse(models=previews)


# ─── Generate ─────────────────────────────────────────────────────────────────

@router.post("/generate", status_code=202, response_model=DbtGenerateResponse)
async def dbt_generate(
    req: DbtGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_or_api_key),
):
    """Parse dbt schema + create Dataset + enqueue synthetic data generation job."""
    _require_pro(current_user)

    # Parse schema
    try:
        models = parse_dbt_schema(req.schema_yaml)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Find requested model
    target = next((m for m in models if m.name == req.model_name), None)
    if target is None:
        available = [m.name for m in models]
        raise HTTPException(
            status_code=422,
            detail=f"Model '{req.model_name}' not found in schema. Available: {available}",
        )

    if not target.columns:
        raise HTTPException(
            status_code=422,
            detail=f"Model '{req.model_name}' has no columns.",
        )

    sdv_metadata = to_sdv_metadata(target)

    # Create a schema_json compatible with the existing pipeline
    # The dbt path creates a Dataset without an actual CSV (no s3_key upload needed —
    # the Celery task will synthesise directly from metadata, no real data to fit from).
    # We store a sentinel s3_key and the schema in schema_json["dbt_metadata"].
    schema_json = {
        "mode": "dbt",
        "model_name": req.model_name,
        "columns": [
            {"name": col.name, "sdtype": col.sdtype, "dtype": col.sdtype, "detected_type": col.sdtype}
            for col in target.columns
        ],
        "pii_columns": [],
        "dbt_metadata": sdv_metadata,
        "dbt_warnings": target.warnings,
    }

    # Persist Dataset with a synthetic s3_key placeholder
    dataset_id = uuid.uuid4()
    s3_key = f"dbt/{uuid.uuid4()}/{req.model_name}.schema"
    dataset = Dataset(
        id=dataset_id,
        original_filename=f"{req.model_name}.schema.yml",
        s3_key=s3_key,
        row_count=0,  # no real data
        schema_json=schema_json,
        user_id=current_user.id,
    )
    db.add(dataset)

    # Create generation job
    job = GenerationJob(
        dataset_id=dataset_id,
        status="queued",
        model_type=req.sdv_model,
        requested_rows=req.row_count,
    )
    db.add(job)
    db.add(UsageEvent(user_id=current_user.id, event_type="generation"))

    await db.commit()
    await db.refresh(dataset)
    await db.refresh(job)

    # Enqueue Celery task — dbt path uses schema_overrides for column types
    schema_overrides = {col.name: col.sdtype for col in target.columns}
    generate_synthetic_data.delay(
        str(job.id),
        str(dataset.id),
        req.sdv_model,
        req.row_count,
        schema_overrides,
    )

    return DbtGenerateResponse(
        dataset_id=dataset.id,
        job_id=job.id,
        status=job.status,
        model_name=req.model_name,
        row_count=req.row_count,
    )
