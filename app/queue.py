from datetime import UTC, datetime, timedelta
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import or_, select

from app.config import settings
from app.db import SessionFactory
from app.models import InferenceEvent, InferenceJob, JobOutbox


def redis_client() -> Redis:
    return cast(Redis, Redis.from_url(settings().redis_url, decode_responses=True))


def job_stream() -> str:
    return f"{settings().stream_prefix}:jobs"


def event_stream(job_id: object) -> str:
    return f"{settings().stream_prefix}:events:{job_id}"


async def ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(job_stream(), "workers", id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def dispatch(redis: Redis) -> None:
    """Publish then mark: duplicates are safe; never mark before successful XADD."""
    async with SessionFactory() as db, db.begin():
        rows = (
            await db.scalars(
                select(JobOutbox)
                .join(InferenceJob, InferenceJob.id == JobOutbox.job_id)
                .where(
                    or_(
                        JobOutbox.published_at.is_(None),
                        JobOutbox.published_at < datetime.now(UTC) - timedelta(seconds=30),
                    ),
                    InferenceJob.next_attempt_at <= datetime.now(UTC),
                    InferenceJob.state.not_in(["REVIEW_REQUIRED", "COMPLETED", "FAILED"]),
                )
                .with_for_update(skip_locked=True, of=JobOutbox)
                .limit(100)
            )
        ).all()
        for row in rows:
            await redis.xadd(job_stream(), {"job_id": str(row.job_id)})
            row.published_at = datetime.now(UTC)
    await dispatch_events(redis)


async def dispatch_events(redis: Redis) -> None:
    async with SessionFactory() as db, db.begin():
        rows = (
            await db.scalars(
                select(InferenceEvent)
                .where(InferenceEvent.published_at.is_(None))
                .order_by(InferenceEvent.created_at, InferenceEvent.id)
                .with_for_update(skip_locked=True)
                .limit(200)
            )
        ).all()
        for row in rows:
            stream = event_stream(row.job_id)
            await redis.xadd(
                stream,
                {
                    "event_id": str(row.id),
                    "job_id": str(row.job_id),
                    "state": row.state,
                    "progress": str(row.progress),
                    "timestamp": row.created_at.isoformat(),
                },
                maxlen=500,
            )
            await redis.expire(stream, 86400)
            row.published_at = datetime.now(UTC)
