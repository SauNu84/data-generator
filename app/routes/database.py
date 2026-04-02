"""
Database connector routes:
  POST /api/connect/database       — connect, list tables (connection string never stored)
  POST /api/connect/database/load  — pull schema + sample rows → create Dataset

Security: connection strings are used once and discarded. Never persisted.

Supported drivers (must be installed):
  - PostgreSQL: psycopg2         (postgresql+psycopg2://)
  - MySQL:      pymysql          (mysql+pymysql://)
"""

from __future__ import annotations

import asyncio
import io
import logging

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user_or_api_key
from app.models import Dataset, UsageEvent
from app.pii import scan_dataframe
from app.schemas import (
    ColumnSchema,
    DatabaseConnectRequest,
    DatabaseConnectResponse,
    DatabaseLoadRequest,
    DatabaseLoadResponse,
    DatabaseTableInfo,
    PiiColumnInfo,
)
from app.storage import upload_csv_bytes

router = APIRouter(prefix="/api/connect", tags=["database"])

log = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("postgresql+psycopg2://", "postgresql://", "mysql+pymysql://", "mysql://")
_MAX_SAMPLE_ROWS = 100_000


def _require_enterprise(user) -> None:
    if user.tier != "enterprise":
        raise HTTPException(
            status_code=402,
            detail="Database connector requires an Enterprise plan.",
        )


def _validate_connection_string(conn: str) -> None:
    """Reject non-DB connection strings and obvious injection attempts."""
    if not any(conn.startswith(s) for s in _ALLOWED_SCHEMES):
        raise HTTPException(
            status_code=422,
            detail=(
                "Unsupported database URL scheme. "
                "Use postgresql+psycopg2:// or mysql+pymysql://"
            ),
        )
    if len(conn) > 1024:
        raise HTTPException(status_code=422, detail="Connection string too long.")


def _list_tables_sync(conn_str: str) -> list[DatabaseTableInfo]:
    """Synchronous: connect, introspect tables, return summary. No data exported."""
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(conn_str, connect_args={"connect_timeout": 10}, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            inspector = inspect(engine)
            table_names = inspector.get_table_names()

            tables = []
            for tbl in table_names:
                columns = [c["name"] for c in inspector.get_columns(tbl)]
                # Rough row count — use COUNT(*) for accuracy
                try:
                    row_count = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar() or 0  # noqa: S608
                except Exception:
                    row_count = -1  # unknown; non-fatal
                tables.append(
                    DatabaseTableInfo(
                        name=tbl,
                        row_count=row_count,
                        column_count=len(columns),
                        columns=columns,
                    )
                )
            return tables
    finally:
        engine.dispose()


def _sample_table_sync(conn_str: str, table: str, sample_rows: int) -> pd.DataFrame:
    """Synchronous: pull up to sample_rows rows from a single table. No full export."""
    from sqlalchemy import create_engine, text

    engine = create_engine(conn_str, connect_args={"connect_timeout": 10}, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            # Parameterise the LIMIT but table name cannot be bound — we validate it from inspector
            df = pd.read_sql(
                text(f"SELECT * FROM {table} LIMIT :limit"),  # noqa: S608
                conn,
                params={"limit": sample_rows},
            )
        return df
    finally:
        engine.dispose()


def _validate_table_name(table: str, known_tables: list[str]) -> None:
    """Reject table names not found via inspector to prevent SQL injection."""
    if table not in known_tables:
        raise HTTPException(
            status_code=422,
            detail=f"Table '{table}' not found. Available: {sorted(known_tables)}",
        )


def _infer_schema(df: pd.DataFrame) -> list[ColumnSchema]:
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


# ─── Connect + list tables ────────────────────────────────────────────────────

@router.post("/database", response_model=DatabaseConnectResponse)
async def connect_database(
    req: DatabaseConnectRequest,
    current_user=Depends(get_current_user_or_api_key),
):
    """Connect to a Postgres or MySQL database and return the table list.

    The connection string is used once and never stored.
    """
    _require_enterprise(current_user)
    _validate_connection_string(req.connection_string)

    try:
        tables = await asyncio.to_thread(_list_tables_sync, req.connection_string)
    except Exception as exc:
        log.warning("DB connect failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to database: {exc}",
        ) from exc

    return DatabaseConnectResponse(tables=tables)


# ─── Load table as Dataset ────────────────────────────────────────────────────

@router.post("/database/load", response_model=DatabaseLoadResponse, status_code=201)
async def load_database_table(
    req: DatabaseLoadRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_or_api_key),
):
    """Pull a sample from a database table and create a Dataset for generation.

    Connection string is used once and never persisted.
    """
    _require_enterprise(current_user)
    _validate_connection_string(req.connection_string)

    if req.sample_rows > _MAX_SAMPLE_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"sample_rows exceeds maximum ({_MAX_SAMPLE_ROWS:,}).",
        )

    # Validate the table name via inspector (prevents SQL injection via table param)
    try:
        known = await asyncio.to_thread(_list_tables_sync, req.connection_string)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not connect: {exc}") from exc

    known_names = [t.name for t in known]
    _validate_table_name(req.table, known_names)

    # Pull sample
    try:
        df = await asyncio.to_thread(_sample_table_sync, req.connection_string, req.table, req.sample_rows)
    except Exception as exc:
        log.warning("DB table load failed table=%s: %s", req.table, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not load table '{req.table}': {exc}",
        ) from exc

    if df.empty:
        raise HTTPException(status_code=422, detail=f"Table '{req.table}' returned no rows.")

    # Upload sample CSV to storage
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    s3_key = upload_csv_bytes(buf.getvalue(), prefix="db-samples")

    schema = _infer_schema(df)
    pii_result = scan_dataframe(df)
    pii_info = [
        PiiColumnInfo(column=c.column, pii_type=c.pii_type, detection_method=c.detection_method)
        for c in pii_result.pii_columns
    ]

    dataset = Dataset(
        original_filename=f"{req.table}.csv",
        s3_key=s3_key,
        row_count=len(df),
        schema_json={
            "columns": [c.model_dump() for c in schema],
            "pii_columns": [p.model_dump() for p in pii_info],
            "mode": "single_table",
            "source": "database",
            "source_table": req.table,
        },
        user_id=current_user.id,
    )
    db.add(dataset)
    db.add(UsageEvent(user_id=current_user.id, event_type="db_load"))
    await db.commit()
    await db.refresh(dataset)

    return DatabaseLoadResponse(
        dataset_id=dataset.id,
        table=req.table,
        row_count=len(df),
        columns=schema,
        pii_columns=pii_info,
    )
