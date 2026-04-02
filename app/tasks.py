"""
Celery tasks for synthetic data generation.

Task flow:
  generate_synthetic_data(job_id, dataset_id, model_type, requested_rows)
    1. Load original CSV from MinIO/S3
    2. Fit SDV model (GaussianCopula or CTGAN)
    3. Sample synthetic rows
    4. Compute quality score (column similarity)
    5. Upload synthetic CSV to MinIO/S3
    6. Update GenerationJob row: status=done, output_s3_key, quality_score_json, expires_at

  cleanup_expired_outputs
    1. Find GenerationJob rows where expires_at < now and status=done
    2. Delete output object from S3/MinIO
    3. Clear output_s3_key (leave row for audit)
"""

import io
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sdv.evaluation.single_table import evaluate_quality
from sdv.metadata import Metadata
from sdv.single_table import CTGANSynthesizer, GaussianCopulaSynthesizer
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.config import settings
from app.models import Dataset, GenerationJob
from app.storage import delete_object, download_object_bytes, upload_dataframe_as_csv

log = logging.getLogger(__name__)


def _make_sync_engine():
    """Build a synchronous SQLAlchemy engine from settings.
    Uses PostgreSQL-specific pool args in prod; falls back for SQLite in tests."""
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    if url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


# Synchronous SQLAlchemy engine for Celery workers (not async)
_sync_engine = _make_sync_engine()


def _get_session() -> Session:
    return Session(_sync_engine)


def _build_quality_score(real_df: pd.DataFrame, synthetic_df: pd.DataFrame, metadata: Metadata) -> dict:
    """Compute column similarity quality score using SDV evaluate_quality."""
    try:
        report = evaluate_quality(real_df, synthetic_df, metadata, verbose=False)
        overall = float(report.get_score() * 100)

        column_scores = []
        details = report.get_details(property_name="Column Shapes")
        if details is not None and not details.empty:
            for _, row in details.iterrows():
                column_scores.append(
                    {"column": str(row.get("Column", row.get("column", "?"))), "score": float(row.get("Score", row.get("score", 0.0)))}
                )
    except Exception as exc:
        log.warning("Quality scoring failed, using fallback: %s", exc)
        overall = 0.0
        column_scores = []

    return {"overall": round(overall, 2), "columns": column_scores}


@celery_app.task(bind=True, name="app.tasks.generate_synthetic_data", max_retries=2, default_retry_delay=30)
def generate_synthetic_data(
    self: Task,
    job_id: str,
    dataset_id: str,
    model_type: str,
    requested_rows: int,
) -> dict:
    """Fit SDV model and generate synthetic CSV for the given job."""
    job_uuid = uuid.UUID(job_id)

    with _get_session() as session:
        job = session.get(GenerationJob, job_uuid)
        if job is None:
            log.error("Job %s not found", job_id)
            return {"status": "failed", "error": "Job not found"}

        # Mark running
        job.status = "running"
        session.commit()

        try:
            dataset = session.get(Dataset, uuid.UUID(dataset_id))
            if dataset is None:
                raise ValueError(f"Dataset {dataset_id} not found")

            # Load original CSV from storage
            log.info("Job %s: loading dataset s3_key=%s", job_id, dataset.s3_key)
            raw = download_object_bytes(dataset.s3_key)
            real_df = pd.read_csv(io.BytesIO(raw))

            # Build SDV metadata
            metadata = Metadata.detect_from_dataframe(real_df)

            # Fit model
            log.info("Job %s: fitting %s on %d rows", job_id, model_type, len(real_df))
            if model_type == "CTGAN":
                synthesizer = CTGANSynthesizer(metadata)
            else:
                synthesizer = GaussianCopulaSynthesizer(metadata)

            synthesizer.fit(real_df)

            # Sample
            log.info("Job %s: sampling %d rows", job_id, requested_rows)
            synthetic_df = synthesizer.sample(requested_rows)

            # Quality score
            quality = _build_quality_score(real_df, synthetic_df, metadata)
            log.info("Job %s: quality=%.1f%%", job_id, quality["overall"])

            # Upload synthetic CSV
            output_key = upload_dataframe_as_csv(synthetic_df, prefix="outputs")
            log.info("Job %s: uploaded synthetic CSV to %s", job_id, output_key)

            # Update job
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.job_output_ttl_seconds)
            job.status = "done"
            job.output_s3_key = output_key
            job.quality_score_json = quality
            job.expires_at = expires_at
            job.completed_at = datetime.now(timezone.utc)
            session.commit()

            return {"status": "done", "output_key": output_key, "quality": quality}

        except SoftTimeLimitExceeded:
            log.error("Job %s: soft time limit exceeded", job_id)
            job.status = "failed"
            job.error_detail = "Generation timed out (soft limit exceeded)"
            job.completed_at = datetime.now(timezone.utc)
            session.commit()
            raise

        except Exception as exc:
            log.exception("Job %s: generation failed: %s", job_id, exc)
            job.status = "failed"
            job.error_detail = str(exc)[:2000]
            job.completed_at = datetime.now(timezone.utc)
            session.commit()
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                return {"status": "failed", "error": str(exc)}


@celery_app.task(name="app.tasks.cleanup_expired_outputs")
def cleanup_expired_outputs() -> dict:
    """Delete S3/MinIO output objects for expired generation jobs."""
    now = datetime.now(timezone.utc)
    deleted = 0

    with _get_session() as session:
        stmt = select(GenerationJob).where(
            GenerationJob.status == "done",
            GenerationJob.expires_at < now,
            GenerationJob.output_s3_key.isnot(None),
        )
        expired_jobs = session.scalars(stmt).all()

        for job in expired_jobs:
            if job.output_s3_key:
                delete_object(job.output_s3_key)
                log.info("Cleanup: deleted %s for job %s", job.output_s3_key, job.id)
                job.output_s3_key = None
                deleted += 1

        session.commit()

    log.info("Cleanup: removed %d expired output(s)", deleted)
    return {"deleted": deleted}
