"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("s3_key", sa.String(512), nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("schema_json", postgresql.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("model_type", sa.String(30), nullable=False, server_default="GaussianCopula"),
        sa.Column("requested_rows", sa.Integer, nullable=False),
        sa.Column("output_s3_key", sa.String(512), nullable=True),
        sa.Column("quality_score_json", postgresql.JSON, nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Index for cleanup task performance
    op.create_index("ix_generation_jobs_expires_at_status", "generation_jobs", ["expires_at", "status"])
    op.create_index("ix_generation_jobs_dataset_id", "generation_jobs", ["dataset_id"])


def downgrade() -> None:
    op.drop_table("generation_jobs")
    op.drop_table("datasets")
