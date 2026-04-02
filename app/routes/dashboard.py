"""
Dashboard routes:
  GET  /api/dashboard         — list user's datasets + job counts (paginated)
  GET  /api/dashboard/{id}    — dataset detail with all jobs
  DELETE /api/dashboard/{id}  — delete dataset and all associated jobs/files
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import Dataset, GenerationJob, User
from app.schemas import DashboardResponse, DatasetSummary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def list_datasets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    offset = (page - 1) * page_size

    # Total count
    total = await db.scalar(
        select(func.count(Dataset.id)).where(Dataset.user_id == user.id)
    )

    # Datasets with job count (subquery)
    job_count_subq = (
        select(GenerationJob.dataset_id, func.count(GenerationJob.id).label("job_count"))
        .group_by(GenerationJob.dataset_id)
        .subquery()
    )

    rows = await db.execute(
        select(Dataset, func.coalesce(job_count_subq.c.job_count, 0).label("job_count"))
        .outerjoin(job_count_subq, Dataset.id == job_count_subq.c.dataset_id)
        .where(Dataset.user_id == user.id)
        .order_by(Dataset.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )

    summaries = []
    for dataset, job_count in rows:
        summaries.append(
            DatasetSummary(
                id=dataset.id,
                original_filename=dataset.original_filename,
                row_count=dataset.row_count,
                created_at=dataset.created_at,
                job_count=job_count,
            )
        )

    return DashboardResponse(
        datasets=summaries,
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    dataset = await db.get(Dataset, dataset_id)
    if not dataset or dataset.user_id != user.id:
        raise HTTPException(status_code=404, detail="Dataset not found.")

    await db.delete(dataset)
    await db.commit()
