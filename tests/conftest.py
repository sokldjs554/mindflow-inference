import os
import subprocess
import sys
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://mindflow:local-demo-only@127.0.0.1:5432/mindflow_test",
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
os.environ["STREAM_PREFIX"] = "mindflow-test"
os.environ["RATE_LIMIT"] = "10000"
os.environ["API_KEY"] = ""
os.environ["RETRY_BASE_SECONDS"] = "0.01"
os.environ["PROVIDER_TIMEOUT_SECONDS"] = "0.1"

from app.db import SessionFactory, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.queue import redis_client  # noqa: E402


@pytest.fixture(scope="session")
def migrated() -> None:
    assert os.environ["DATABASE_URL"].rsplit("/", 1)[-1].endswith("_test"), (
        "Use a dedicated _test DB"
    )
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


@pytest_asyncio.fixture
async def client(migrated: None) -> AsyncIterator[httpx.AsyncClient]:
    await engine.dispose()
    async with SessionFactory() as db, db.begin():
        tables = ",".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
        await db.execute(text(f"TRUNCATE {tables} CASCADE"))
    redis = redis_client()
    # Delete only this test namespace, never FLUSHDB an arbitrary Redis instance.
    keys = [k async for k in redis.scan_iter("mindflow-test:*")]
    if keys:
        await redis.delete(*keys)
    await redis.aclose()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as value:
            yield value
