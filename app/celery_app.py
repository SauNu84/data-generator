from celery import Celery

from app.config import settings

celery_app = Celery(
    "datagen",
    broker=settings.redis_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=settings.job_output_ttl_seconds,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,       # 30-min hard kill
    task_soft_time_limit=1500,  # 25-min graceful stop
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # Cleanup expired output files every hour
        "cleanup-expired-outputs": {
            "task": "app.tasks.cleanup_expired_outputs",
            "schedule": 3600.0,
        },
    },
)
