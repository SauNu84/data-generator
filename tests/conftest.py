"""
Pytest fixtures for the synthetic data generator backend.

Test strategy:
- Use SQLite (in-memory) via SQLAlchemy for DB — no Postgres needed in CI
- Use a mock S3 client (moto) for storage
- Celery tasks are called directly (no broker) using task.apply() in eager mode
"""

import io
import os
import uuid

import pandas as pd
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set env before importing app modules
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")

from app.database import Base, get_db
from app.main import app
from app.models import Dataset, GenerationJob

TEST_ENGINE = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionLocal = async_sessionmaker(TEST_ENGINE, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client(db_session):
    """Async test client with DB dependency overridden."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Patch ensure_bucket to no-op
    import app.main as main_module
    main_module.ensure_bucket = lambda: None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def sample_csv_bytes() -> bytes:
    """Small valid CSV fixture (10 rows, 3 cols)."""
    df = pd.DataFrame(
        {
            "age": [25, 30, 35, 40, 45, 50, 55, 60, 65, 70],
            "income": [50000, 60000, 70000, 80000, 90000, 100000, 110000, 120000, 130000, 140000],
            "category": ["A", "B", "A", "C", "B", "A", "C", "B", "A", "C"],
        }
    )
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def sample_dataset(db_session) -> Dataset:
    """Pre-inserted Dataset fixture (sync — used in non-async tests via run)."""
    import asyncio

    dataset = Dataset(
        id=uuid.uuid4(),
        original_filename="test.csv",
        s3_key="inputs/test.csv",
        row_count=10,
        schema_json=[
            {"name": "age", "sdtype": "numerical", "dtype": "int64"},
            {"name": "income", "sdtype": "numerical", "dtype": "int64"},
            {"name": "category", "sdtype": "categorical", "dtype": "object"},
        ],
    )

    async def _insert():
        db_session.add(dataset)
        await db_session.commit()
        await db_session.refresh(dataset)

    asyncio.get_event_loop().run_until_complete(_insert())
    return dataset
