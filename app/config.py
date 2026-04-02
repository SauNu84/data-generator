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

    # ── Auth / JWT ────────────────────────────────────────────────────────────
    jwt_secret_key: str = "CHANGE_ME_IN_PRODUCTION_USE_256BIT_RANDOM"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    email_token_expire_hours: int = 24

    # ── Google OAuth 2.0 ─────────────────────────────────────────────────────
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"

    # ── App (for email links) ─────────────────────────────────────────────────
    app_base_url: str = "http://localhost:3000"
    backend_base_url: str = "http://localhost:8000"

    # ── Stripe ───────────────────────────────────────────────────────────────
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_pro_price_id: str = ""       # price_xxx for $49/mo Pro plan

    # ── Free tier limits ──────────────────────────────────────────────────────
    free_tier_monthly_generations: int = 10


settings = Settings()
