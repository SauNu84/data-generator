from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.dev", env_file_encoding="utf-8", extra="ignore")

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://datagen:datagen@localhost:5432/datagen"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # MinIO / S3
    s3_endpoint_url: str | None = None  # None → real AWS S3
    aws_access_key_id: str = "minioadmin"
    aws_secret_access_key: str = "minioadmin"
    s3_bucket_name: str = "datagen-files"
    s3_presigned_url_expiry: int = 86400  # 24h

    # Upload limits
    max_upload_bytes: int = 50 * 1024 * 1024  # 50 MB
    max_upload_rows: int = 100_000

    # Job TTL (seconds) — used by cleanup task
    job_output_ttl_seconds: int = 86400  # 24h


settings = Settings()
