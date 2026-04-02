"""
Multi-table synthesis routes:
  POST /api/upload/multi-table              — upload ZIP + relationships.json, create Dataset
  POST /api/multi-table/{dataset_id}/generate — enqueue HMA synthesis job
"""

from __future__ import annotations

import io
import json
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

import pandas as pd

from app.database import get_db
from app.deps import get_current_user_or_api_key
from app.models import Dataset, GenerationJob, UsageEvent
from app.schemas import (
    MultiTableJobRequest,
    MultiTableJobResponse,
    MultiTableRelationship,
    MultiTableUploadResponse,
    TableSchemaPreview,
)
from app.storage import upload_csv_bytes

router = APIRouter(tags=["multi-table"])

_MAX_ZIP_BYTES = 100 * 1024 * 1024  # 100 MB
_MAX_TABLES = 20


def _require_enterprise(user) -> None:
    if user.tier != "enterprise":
        raise HTTPException(
            status_code=402,
            detail="Multi-table synthesis requires an Enterprise plan.",
        )


# ─── Upload ZIP ───────────────────────────────────────────────────────────────

@router.post("/api/upload/multi-table", response_model=MultiTableUploadResponse, status_code=201)
async def upload_multi_table(
    file: UploadFile = File(..., description="ZIP archive containing one CSV per table"),
    relationships: str = Form(..., description="JSON array of FK relationships (MultiTableRelationship[])"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_or_api_key),
):
    """Accept a ZIP of CSVs + a relationships JSON string, create a multi-table Dataset."""
    _require_enterprise(current_user)

    raw = await file.read()
    if len(raw) > _MAX_ZIP_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"ZIP too large. Maximum {_MAX_ZIP_BYTES // (1024*1024)} MB.",
        )

    # Parse relationships
    try:
        rels_data = json.loads(relationships)
        parsed_rels = [MultiTableRelationship(**r) for r in rels_data]
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid relationships JSON: {exc}") from exc

    # Extract CSV files from ZIP
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Not a valid ZIP file: {exc}") from exc

    csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv") and not n.startswith("__MACOSX")]
    if not csv_names:
        raise HTTPException(status_code=422, detail="ZIP contains no CSV files.")
    if len(csv_names) > _MAX_TABLES:
        raise HTTPException(status_code=422, detail=f"ZIP contains too many tables (max {_MAX_TABLES}).")

    # Validate and read tables
    tables: dict[str, pd.DataFrame] = {}
    schema_preview: dict[str, TableSchemaPreview] = {}
    for name in csv_names:
        table_name = name.rsplit("/", 1)[-1].removesuffix(".csv")
        try:
            df = pd.read_csv(io.BytesIO(zf.read(name)))
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not parse '{name}': {exc}") from exc
        if df.empty:
            raise HTTPException(status_code=422, detail=f"Table '{table_name}' is empty.")
        tables[table_name] = df
        schema_preview[table_name] = TableSchemaPreview(row_count=len(df), columns=len(df.columns))

    # Validate relationships reference known tables
    all_tables = set(tables)
    for rel in parsed_rels:
        for tbl in (rel.parent_table, rel.child_table):
            if tbl not in all_tables:
                raise HTTPException(
                    status_code=422,
                    detail=f"Relationship references unknown table '{tbl}'. Known: {sorted(all_tables)}",
                )

    # Store ZIP as-is
    s3_key = upload_csv_bytes(raw, prefix="multi-table")

    schema_json = {
        "mode": "multi_table",
        "tables": list(tables),
        "relationships": [r.model_dump() for r in parsed_rels],
        "schema_preview": {k: v.model_dump() for k, v in schema_preview.items()},
    }

    dataset = Dataset(
        original_filename=file.filename or "multi-table.zip",
        s3_key=s3_key,
        row_count=sum(len(df) for df in tables.values()),
        schema_json=schema_json,
        user_id=current_user.id,
    )
    db.add(dataset)
    db.add(UsageEvent(user_id=current_user.id, event_type="multi_table_upload"))
    await db.commit()
    await db.refresh(dataset)

    return MultiTableUploadResponse(
        dataset_id=dataset.id,
        tables=list(tables),
        table_count=len(tables),
        relationship_count=len(parsed_rels),
        schema_preview=schema_preview,
    )


# ─── Generate ─────────────────────────────────────────────────────────────────

@router.post("/api/multi-table/{dataset_id}/generate", response_model=MultiTableJobResponse, status_code=202)
async def generate_multi_table(
    dataset_id: str,
    req: MultiTableJobRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_or_api_key),
):
    """Enqueue an HMA multi-table synthesis job for the given Dataset."""
    import uuid as _uuid
    _require_enterprise(current_user)

    try:
        ds_uuid = _uuid.UUID(dataset_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid dataset_id.") from exc

    dataset = await db.get(Dataset, ds_uuid)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if dataset.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied.")

    schema_meta = dataset.schema_json or {}
    if schema_meta.get("mode") != "multi_table":
        raise HTTPException(status_code=422, detail="Dataset is not a multi-table dataset.")

    tables = schema_meta.get("tables", [])
    if not tables:
        raise HTTPException(status_code=422, detail="Dataset has no tables.")

    job = GenerationJob(
        dataset_id=dataset.id,
        status="queued",
        model_type="HMA",
        requested_rows=0,  # HMA uses scale_factor, not a fixed row count
    )
    db.add(job)
    db.add(UsageEvent(user_id=current_user.id, event_type="generation"))
    await db.commit()
    await db.refresh(job)

    # Enqueue HMA Celery task
    from app.tasks import generate_multi_table_data
    generate_multi_table_data.delay(
        str(job.id),
        str(dataset.id),
        req.scale_factor,
    )

    return MultiTableJobResponse(
        job_id=job.id,
        status=job.status,
        dataset_id=dataset.id,
        estimated_tables=len(tables),
        scale_factor=req.scale_factor,
    )
