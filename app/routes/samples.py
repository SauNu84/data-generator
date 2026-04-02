"""
Sample dataset template routes:
  GET  /api/samples              — list available pre-built templates
  POST /api/samples/{id}/load    — ingest a template as a Dataset (same flow as CSV upload)
"""

from __future__ import annotations

import io
import pathlib

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user_or_api_key
from app.models import Dataset, UsageEvent
from app.pii import scan_dataframe
from app.schemas import (
    ColumnSchema,
    PiiColumnInfo,
    SampleLoadResponse,
    SampleTemplate,
    SamplesListResponse,
)
from app.storage import upload_csv_bytes

router = APIRouter(prefix="/api/samples", tags=["samples"])

_SAMPLES_DIR = pathlib.Path(__file__).parent.parent / "samples"

# Template catalogue — kept in sync with files under app/samples/
_TEMPLATES: list[dict] = [
    {
        "id": "ecommerce_orders",
        "name": "E-commerce Orders",
        "description": "Order transactions: order_id, customer_id, product_id, status, amount, quantity, timestamps.",
        "file": "ecommerce_orders.csv",
    },
    {
        "id": "hr_employees",
        "name": "HR Employees",
        "description": "Employee roster: employee_id, department, title, salary, hire_date, performance_score.",
        "file": "hr_employees.csv",
    },
    {
        "id": "fintech_transactions",
        "name": "Fintech Transactions",
        "description": "Financial transactions: transaction_id, account_id, amount, type, currency, merchant, status.",
        "file": "fintech_transactions.csv",
    },
    {
        "id": "healthcare_visits",
        "name": "Healthcare Visits",
        "description": "Patient visit records (HIPAA-safe synthetic): visit_id, age, diagnosis_code, visit_type, charges.",
        "file": "healthcare_visits.csv",
    },
]

_TEMPLATE_INDEX: dict[str, dict] = {t["id"]: t for t in _TEMPLATES}


def _load_csv(template: dict) -> pd.DataFrame:
    path = _SAMPLES_DIR / template["file"]
    if not path.exists():
        raise FileNotFoundError(f"Sample file not found: {path}")
    return pd.read_csv(path)


def _infer_schema(df: pd.DataFrame) -> list[ColumnSchema]:
    """Reuse the same schema inference logic as main.py upload_csv."""
    from sdv.metadata import Metadata

    meta = Metadata.detect_from_dataframe(df)
    columns_meta: dict = {}
    try:
        meta_dict = meta.to_dict()
        tables = meta_dict.get("tables") or {}
        for tbl in tables.values():
            columns_meta = tbl.get("columns", {})
            break
        if not columns_meta:
            columns_meta = meta_dict.get("columns", {})
    except Exception:
        columns_meta = {}

    return [
        ColumnSchema(name=col, sdtype=columns_meta.get(col, {}).get("sdtype", "categorical"), dtype=str(df[col].dtype))
        for col in df.columns
    ]


# ─── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=SamplesListResponse)
def list_samples():
    """Return all available sample dataset templates."""
    templates = []
    for t in _TEMPLATES:
        try:
            df = _load_csv(t)
            templates.append(
                SampleTemplate(
                    id=t["id"],
                    name=t["name"],
                    description=t["description"],
                    row_count=len(df),
                    column_count=len(df.columns),
                )
            )
        except FileNotFoundError:
            # Skip templates whose CSV is missing rather than crashing
            pass
    return SamplesListResponse(templates=templates)


# ─── Load ─────────────────────────────────────────────────────────────────────

@router.post("/{template_id}/load", response_model=SampleLoadResponse, status_code=201)
async def load_sample(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_or_api_key),
):
    """Ingest a sample template as a Dataset so it can be used for generation."""
    template = _TEMPLATE_INDEX.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found.")

    try:
        df = _load_csv(template)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Upload CSV bytes to storage
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    s3_key = upload_csv_bytes(buf.getvalue(), prefix="samples")

    # Schema inference + PII scan
    schema = _infer_schema(df)
    pii_result = scan_dataframe(df)
    pii_info = [
        PiiColumnInfo(column=c.column, pii_type=c.pii_type, detection_method=c.detection_method)
        for c in pii_result.pii_columns
    ]

    dataset = Dataset(
        original_filename=template["file"],
        s3_key=s3_key,
        row_count=len(df),
        schema_json={
            "columns": [c.model_dump() for c in schema],
            "pii_columns": [p.model_dump() for p in pii_info],
            "mode": "single_table",
            "template_id": template_id,
        },
        user_id=current_user.id,
    )
    db.add(dataset)
    db.add(UsageEvent(user_id=current_user.id, event_type="sample_load"))
    await db.commit()
    await db.refresh(dataset)

    return SampleLoadResponse(
        dataset_id=dataset.id,
        template_id=template_id,
        original_filename=template["file"],
        row_count=dataset.row_count,
        columns=schema,
        pii_columns=pii_info,
    )
