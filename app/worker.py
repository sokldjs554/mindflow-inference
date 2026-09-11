import asyncio
import contextlib
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta

from prometheus_client import start_http_server
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.clinical import generate as generate_clinical
from app.config import settings
from app.db import SessionFactory, engine
from app.errors import DomainError
from app.models import (
    AudioAsset,
    EvidenceLink,
    GeneratedNote,
    GeneratedSection,
    InferenceEvent,
    InferenceJob,
    InferenceRun,
    JobOutbox,
    PromptVersion,
    ValidationResult,
)
from app.observability import (
    INFERENCE_LATENCY,
    JOB_FAILURES,
    PROVIDER_FAILURES,
    VALIDATION_FAILURES,
    log,
)
from app.providers import (
    DeterministicEvidenceValidator,
    DeterministicRedactor,
    EvidenceValidationProvider,
    LLMProvider,
    MockLLMProvider,
    MockSTTProvider,
    RedactionProvider,
    STTProvider,
)
from app.queue import dispatch, dispatch_events, ensure_group, job_stream, redis_client
from app.schemas import NoteOutput, TranscriptInput
from app.services import append_transcript, digest, require_session, transcript

TERMINAL = {"REVIEW_REQUIRED", "COMPLETED", "FAILED"}
PROGRESS = {
    "INGESTING": 10,
    "TRANSCRIBING": 20,
    "REDACTING": 35,
    "GENERATING": 60,
    "VALIDATING": 85,
    "REVIEW_REQUIRED": 100,
    "FAILED": 100,
}


class Worker:
    def __init__(
        self,
        redis: Redis,
        llm: LLMProvider | None = None,
        stt: STTProvider | None = None,
        redactor: RedactionProvider | None = None,
        validator: EvidenceValidationProvider | None = None,
    ):
        self.redis = redis
        self.llm = llm or MockLLMProvider()
        self.stt = stt or MockSTTProvider()
        self.redactor = redactor or DeterministicRedactor()
        self.validator = validator or DeterministicEvidenceValidator()
        self.consumer = str(uuid.uuid4())
        self.recovery_cursor = "0-0"

    async def transition(self, job_id: uuid.UUID, state: str) -> None:
        async with SessionFactory() as db, db.begin():
            job = await db.get(InferenceJob, job_id)
            assert job is not None
            job.state, job.progress = state, PROGRESS[state]
            db.add(InferenceEvent(job_id=job_id, state=state, progress=job.progress))
            log("job_transition", job_id=job_id, correlation_id=job.correlation_id, state=state)
        # DB state survives a Redis outage. Dispatcher retries unsent events later.
        with contextlib.suppress(RedisError, ConnectionError, OSError):
            await dispatch_events(self.redis)

    async def process(self, job_id: uuid.UUID) -> bool:
        # Connection-level advisory lock prevents parallel handling of redelivered messages.
        # It is released by PostgreSQL if the process dies; no expiring-lock fencing race.
        async with engine.connect() as connection:
            lock_key = int.from_bytes(job_id.bytes[:8], "big", signed=True)
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}
            )
            await connection.commit()
            if not acquired:
                return False
            try:
                return await self._process(job_id)
            finally:
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
                await connection.commit()

    async def _process(self, job_id: uuid.UUID) -> bool:
        async with SessionFactory() as db, db.begin():
            job = await db.get(InferenceJob, job_id)
            if job is None or job.state in TERMINAL:
                return True
            if job.next_attempt_at > datetime.now(UTC):
                return False
            if job.attempts >= settings().max_attempts:
                job.state, job.error_code, job.failed_at = (
                    "FAILED",
                    "ATTEMPTS_EXHAUSTED",
                    datetime.now(UTC),
                )
                job.progress = 100
                JOB_FAILURES.labels("ATTEMPTS_EXHAUSTED").inc()
                db.add(InferenceEvent(job_id=job.id, state="FAILED", progress=100))
                return True
            job.attempts += 1
            job.started_at = job.started_at or datetime.now(UTC)
            snapshot, audio_id = job.input_snapshot, job.audio_id
            stt_metadata = job.stt_metadata
            model_version, prompt_version = job.model_version, job.prompt_version
            attempts, started_at, session_id = job.attempts, job.started_at, job.session_id
            correlation_id = job.correlation_id
            prompt = await db.get(PromptVersion, prompt_version)
            assert prompt is not None
            template = prompt.template
        start = time.perf_counter()
        try:
            llm_metadata = self.llm.metadata.model_dump()
            redactor_metadata = self.redactor.metadata.model_dump()
            validator_metadata = self.validator.metadata.model_dump()
            await self.transition(job_id, "INGESTING")
            if audio_id and not snapshot:
                await self.transition(job_id, "TRANSCRIBING")
                async with SessionFactory() as db:
                    audio = await db.get(AudioAsset, audio_id)
                    assert audio is not None
                    audio_bytes = audio.data
                stt_metadata = self.stt.metadata.model_dump()
                snapshot = await asyncio.wait_for(
                    self.stt.transcribe(audio_bytes), settings().provider_timeout_seconds
                )
                parsed = TranscriptInput.model_validate(
                    {
                        "utterances": [
                            {k: u[k] for k in ("sequence", "speaker", "text")} for u in snapshot
                        ]
                    }
                )
                async with SessionFactory() as db, db.begin():
                    await require_session(db, session_id, lock=True)
                    if await transcript(db, session_id):
                        raise DomainError(
                            "INPUT_CONFLICT", "Transcript changed during audio ingestion"
                        )
                    await append_transcript(db, session_id, parsed, correlation_id)
                    await db.flush()
                    snapshot = await transcript(db, session_id)
                    job = await db.get(InferenceJob, job_id)
                    assert job is not None
                    job.input_snapshot = snapshot
                    job.stt_metadata = stt_metadata
            await self.transition(job_id, "REDACTING")
            redacted = self.redactor.redact(snapshot).transcript
            await self.transition(job_id, "GENERATING")
            raw = await asyncio.wait_for(
                self.llm.generate(redacted, template, model_version),
                settings().provider_timeout_seconds,
            )
            output = NoteOutput.model_validate(raw)
            await self.transition(job_id, "VALIDATING")
            validated = [
                (kind, pos, statement, *self.validator.validate(statement, redacted))
                for kind in ("subjective", "objective", "plan")
                for pos, statement in enumerate(getattr(output, kind))
            ]
            counts: dict[str, int] = {}
            for _, _, _, status, _ in validated:
                counts[status] = counts.get(status, 0) + 1
                if status != "SUPPORTED":
                    VALIDATION_FAILURES.labels(status).inc()
            summary = {
                "evidence_coverage": counts.get("SUPPORTED", 0) / len(validated),
                "unsupported_count": counts.get("UNSUPPORTED", 0),
                "contradiction_count": counts.get("CONTRADICTED", 0),
                "stale_count": counts.get("STALE_EVIDENCE", 0),
                "partial_count": counts.get("PARTIALLY_SUPPORTED", 0),
                "schema_validity": True,
                "statement_count": len(validated),
            }
            latency = (time.perf_counter() - start) * 1000
            async with SessionFactory() as db, db.begin():
                run = InferenceRun(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    session_id=session_id,
                    model_version=model_version,
                    prompt_version=prompt_version,
                    input_hash=digest(snapshot),
                    redacted_input_hash=digest(redacted),
                    redacted_snapshot=redacted,
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                    latency_ms=latency,
                    retry_count=attempts - 1,
                    validation_summary=summary,
                    provider_metadata={
                        "mode": llm_metadata["mode"],
                        "deterministic": all(
                            m["deterministic"]
                            for m in [llm_metadata, redactor_metadata, validator_metadata]
                            + ([stt_metadata] if stt_metadata else [])
                        ),
                        "redactor": redactor_metadata["version"],
                        "validator": validator_metadata["version"],
                        "providers": {
                            "llm": llm_metadata,
                            "stt": stt_metadata,
                            "redaction": redactor_metadata,
                            "evidence_validation": validator_metadata,
                        },
                        "clinical_support": generate_clinical(redacted).model_dump(mode="json"),
                        "prompt_hash": digest(template),
                        "audio_fixture": bool(stt_metadata and stt_metadata["mode"] == "mock"),
                    },
                )
                db.add(run)
                await db.flush()
                note = GeneratedNote(id=uuid.uuid4(), run_id=run.id)
                db.add(note)
                await db.flush()
                for kind, pos, statement, status, reason in validated:
                    section = GeneratedSection(
                        id=uuid.uuid4(),
                        note_id=note.id,
                        kind=kind,
                        position=pos,
                        text=statement.text,
                    )
                    db.add(section)
                    await db.flush()
                    db.add(ValidationResult(section_id=section.id, status=status, reason=reason))
                    for sequence in sorted(set(statement.evidence)):
                        db.add(EvidenceLink(section_id=section.id, sequence=sequence))
                job = await db.get(InferenceJob, job_id)
                assert job is not None
                job.state, job.progress = "REVIEW_REQUIRED", 100
                job.completed_at, job.error_code = datetime.now(UTC), None
                db.add(InferenceEvent(job_id=job.id, state="REVIEW_REQUIRED", progress=100))
                log(
                    "inference_finished",
                    job_id=job.id,
                    run_id=run.id,
                    correlation_id=job.correlation_id,
                )
            INFERENCE_LATENCY.observe(latency / 1000)
            return True
        except SQLAlchemyError:
            log("job_storage_unavailable", job_id=job_id, code="DATABASE_UNAVAILABLE")
            return False
        except DomainError as exc:
            await self.failure(job_id, exc.code, retryable=False)
            return True
        except Exception as exc:
            # Never persist provider exception text: it can contain input or credentials.
            code = (
                "PROVIDER_TIMEOUT"
                if isinstance(exc, TimeoutError)
                else ("SCHEMA_INVALID" if isinstance(exc, ValidationError) else "PROVIDER_FAILURE")
            )
            PROVIDER_FAILURES.labels(code).inc()
            await self.failure(job_id, code, retryable=code != "SCHEMA_INVALID")
            return True

    async def failure(self, job_id: uuid.UUID, code: str, retryable: bool) -> None:
        async with SessionFactory() as db, db.begin():
            job = await db.get(InferenceJob, job_id)
            assert job is not None
            job.error_code = code
            retry = retryable and job.attempts < settings().max_attempts
            job.state, job.progress = ("QUEUED", 0) if retry else ("FAILED", 100)
            if retry:
                job.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=settings().retry_base_seconds * 2 ** (job.attempts - 1)
                )
                outbox = await db.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
                assert outbox is not None
                outbox.published_at = None
            else:
                job.failed_at = datetime.now(UTC)
                JOB_FAILURES.labels(code).inc()
            db.add(InferenceEvent(job_id=job.id, state=job.state, progress=job.progress))
            log(
                "job_attempt_failed",
                job_id=job.id,
                code=code,
                attempt=job.attempts,
                correlation_id=job.correlation_id,
            )

    async def tick(self, block_ms: int = 500) -> int:
        await ensure_group(self.redis)
        await dispatch(self.redis)
        recovered = await self.redis.xautoclaim(
            job_stream(),
            "workers",
            self.consumer,
            min_idle_time=2000,
            start_id=self.recovery_cursor,
            count=10,
        )
        self.recovery_cursor = recovered[0]
        messages = recovered[1]
        if not messages:
            batches = await self.redis.xreadgroup(
                "workers", self.consumer, {job_stream(): ">"}, count=1, block=block_ms
            )
            messages = batches[0][1] if batches else []
        for message_id, fields in messages:
            try:
                job_id = uuid.UUID(fields["job_id"])
            except (KeyError, ValueError):
                await self.redis.xack(job_stream(), "workers", message_id)
                await self.redis.xdel(job_stream(), message_id)
                continue
            if await self.process(job_id):
                await self.redis.xack(job_stream(), "workers", message_id)
                # Single consumer group: delete acknowledged entries, never trim pending jobs.
                await self.redis.xdel(job_stream(), message_id)
        await dispatch_events(self.redis)
        return len(messages)


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    redis = redis_client()
    worker = Worker(redis)
    metrics_server, _ = start_http_server(
        settings().worker_metrics_port, addr=settings().worker_metrics_host
    )
    log("worker_started")
    try:
        while not stop.is_set():
            try:
                await worker.tick()
            except Exception:
                log("worker_infrastructure_error", code="DEPENDENCY_UNAVAILABLE")
                await asyncio.sleep(1)
    finally:
        await asyncio.to_thread(metrics_server.shutdown)
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
