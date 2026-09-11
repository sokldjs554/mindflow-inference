import hashlib
import io
import uuid
import wave
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.db import get_db
from app.errors import DomainError
from app.models import AudioAsset, AuditEvent, InferenceJob, InferenceRun, Session
from app.schemas import (
    AudioView,
    AuditView,
    ComparisonView,
    ErrorEnvelope,
    EvidenceView,
    InferenceCreate,
    JobView,
    Page,
    ResultView,
    ReviewInput,
    RunListItem,
    SessionCreate,
    SessionDetail,
    SessionView,
    TranscriptInput,
    TranscriptView,
)


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        401: "UNAUTHORIZED: a valid X-API-Key is required when configured.",
        404: "The requested session, job, run or audio resource does not exist.",
        409: "State, evidence approval, input or idempotency constraint conflict.",
        413: "Request body, transcript or audio exceeds the configured size limit.",
        415: "UNSUPPORTED_AUDIO: only PCM WAV content types are accepted.",
        422: "Invalid request schema, cursor, transcript or WAV content.",
        429: "RATE_LIMITED: retry after the current one-minute window.",
        503: "DEPENDENCY_UNAVAILABLE: a required service is unavailable.",
    }
    return {code: {"model": ErrorEnvelope, "description": descriptions[code]} for code in codes}


router = APIRouter(prefix="/api", responses=error_responses(401, 413, 422, 429, 503))
DB = Annotated[AsyncSession, Depends(get_db)]
Key = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$"),
]


@router.post(
    "/sessions",
    response_model=SessionView,
    status_code=201,
    tags=["Sessions"],
    description="Create a synthetic documentation session. No clinical decisions.",
)
async def create_session(body: SessionCreate, db: DB) -> Session:
    session = Session(title=body.title)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get(
    "/sessions",
    response_model=Page[SessionView],
    tags=["Sessions"],
    description="List sessions newest first using an opaque timestamp/UUID keyset cursor.",
)
async def sessions(
    db: DB, limit: int = Query(20, ge=1, le=100), cursor: str | None = Query(None, max_length=200)
) -> dict[str, Any]:
    return await services.list_page(db, Session, limit, cursor)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetail,
    tags=["Sessions"],
    responses=error_responses(404),
    description="Read a session and its original append-only transcript with correction links.",
)
async def session_detail(session_id: uuid.UUID, db: DB) -> dict[str, Any]:
    session = await services.require_session(db, session_id)
    return {
        **SessionView.model_validate(session).model_dump(mode="json"),
        "utterances": await services.transcript(db, session_id),
    }


@router.post(
    "/sessions/{session_id}/transcript",
    response_model=TranscriptView,
    responses=error_responses(404, 409),
    status_code=201,
    tags=["Input"],
    description="Append immutable utterances. corrects explicitly supersedes a prior sequence.",
)
async def submit_transcript(
    session_id: uuid.UUID, body: TranscriptInput, request: Request, db: DB
) -> dict[str, Any]:
    await services.append_transcript(db, session_id, body, request.state.correlation_id)
    await db.commit()
    return {"utterances": await services.transcript(db, session_id)}


@router.post(
    "/sessions/{session_id}/audio",
    response_model=AudioView,
    responses=error_responses(404, 415),
    status_code=201,
    tags=["Input"],
    description="Upload PCM WAV <=5 MB and 120 seconds. Mock STT returns a fixed fixture.",
)
async def upload_audio(
    session_id: uuid.UUID, db: DB, file: Annotated[UploadFile, File()]
) -> dict[str, str]:
    await services.require_session(db, session_id)
    if file.content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise DomainError("UNSUPPORTED_AUDIO", "Only PCM WAV audio is accepted", 415)
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        raise DomainError("AUDIO_TOO_LARGE", "Audio exceeds 5 MB", 413)
    try:
        with wave.open(io.BytesIO(data)) as audio:
            if audio.getnframes() == 0 or audio.getnframes() / audio.getframerate() > 120:
                raise ValueError
            if len(audio.readframes(audio.getnframes())) != (
                audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
            ):
                raise ValueError
    except (wave.Error, EOFError, ValueError) as exc:
        raise DomainError("INVALID_AUDIO", "Invalid or oversized-duration PCM WAV", 422) from exc
    asset = AudioAsset(
        session_id=session_id,
        content_type="audio/wav",
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
    )
    db.add(asset)
    await db.commit()
    return {"audio_id": str(asset.id), "stt_mode": "mock-fixed-synthetic-fixture"}


@router.post(
    "/sessions/{session_id}/inferences",
    response_model=JobView,
    status_code=202,
    tags=["Inference"],
    responses=error_responses(404, 409),
    description="Atomically snapshot input and enqueue a background job. Idempotency-Key reuses "
    "the same job for identical requests and rejects different requests using that key.",
)
async def inference(
    session_id: uuid.UUID, body: InferenceCreate, request: Request, db: DB, idempotency_key: Key
) -> InferenceJob:
    job = await services.create_job(
        db, session_id, body, idempotency_key, request.state.correlation_id
    )
    await db.commit()
    return job


@router.get(
    "/jobs/{job_id}",
    response_model=JobView,
    tags=["Inference"],
    responses=error_responses(404),
    description="Read durable job state, progress, attempt count and sanitized failure code.",
)
async def get_job(job_id: uuid.UUID, db: DB) -> InferenceJob:
    job = await db.get(InferenceJob, job_id)
    if job is None:
        raise DomainError("JOB_NOT_FOUND", "Inference job does not exist", 404)
    return job


@router.get(
    "/jobs/{job_id}/result",
    response_model=ResultView,
    tags=["Inference"],
    responses=error_responses(404, 409),
    description="Read the validated result for a job; returns RESULT_NOT_READY until a "
    "successful inference has persisted a result, including for failed jobs.",
)
async def job_result(job_id: uuid.UUID, db: DB) -> dict[str, Any]:
    await get_job(job_id, db)
    run = await db.scalar(select(InferenceRun).where(InferenceRun.job_id == job_id))
    if run is None:
        raise DomainError("RESULT_NOT_READY", "No validated result is available", 409)
    return await services.result(db, run.id)


@router.get(
    "/sessions/{session_id}/runs",
    response_model=Page[RunListItem],
    tags=["Inference"],
    responses=error_responses(404),
    description="List successful inference runs for a session using descending keyset pagination.",
)
async def runs(
    session_id: uuid.UUID,
    db: DB,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=200),
) -> dict[str, Any]:
    await services.require_session(db, session_id)
    return await services.list_page(db, InferenceRun, limit, cursor, session_id)


@router.get(
    "/runs/{run_id}",
    response_model=ResultView,
    tags=["Inference"],
    responses=error_responses(404),
    description="Read immutable generated statements and provenance with current correction-aware "
    "validation and human review status.",
)
async def get_result(run_id: uuid.UUID, db: DB) -> dict[str, Any]:
    return await services.result(db, run_id)


@router.get(
    "/runs/{run_id}/evidence",
    response_model=EvidenceView,
    tags=["Evidence"],
    responses=error_responses(404),
    description="Inspect statement-to-sequence bindings against the redacted source snapshot; "
    "invalid references remain visible for review.",
)
async def evidence(run_id: uuid.UUID, db: DB) -> dict[str, Any]:
    output = await services.result(db, run_id)
    return {"statements": output["statements"], "source": output["source"]}


@router.post(
    "/runs/{run_id}/reviews",
    responses=error_responses(404, 409),
    response_model=ResultView,
    tags=["Review"],
    description="Human gate: approve only fully supported active evidence, or reject.",
)
async def review(run_id: uuid.UUID, body: ReviewInput, request: Request, db: DB) -> dict[str, Any]:
    value = await services.review(db, run_id, body, request.state.correlation_id)
    await db.commit()
    return value


@router.post(
    "/runs/{run_id}/replay",
    responses=error_responses(404, 409),
    response_model=JobView,
    status_code=202,
    tags=["Replay"],
    description="Re-execute immutable original input with a selected model/prompt version.",
)
async def replay(
    run_id: uuid.UUID, body: InferenceCreate, request: Request, db: DB, idempotency_key: Key
) -> InferenceJob:
    original = await db.get(InferenceRun, run_id)
    if original is None:
        raise DomainError("RUN_NOT_FOUND", "Original run does not exist", 404)
    if body.audio_id:
        raise DomainError("REPLAY_INPUT_IMMUTABLE", "Replay cannot replace original audio", 422)
    job = await services.create_job(
        db,
        original.session_id,
        body,
        idempotency_key,
        request.state.correlation_id,
        replay=original,
    )
    await db.commit()
    return job


@router.get(
    "/comparisons",
    response_model=ComparisonView,
    tags=["Replay"],
    responses=error_responses(404, 409),
    description="Compare measured validation metrics and latency for two successful runs "
    "with identical input hashes and session ownership.",
)
async def comparison(original: uuid.UUID, replay: uuid.UUID, db: DB) -> dict[str, Any]:
    return await services.compare(db, original, replay)


@router.get(
    "/audit/{resource_id}",
    tags=["Review"],
    response_model=list[AuditView],
    response_model_exclude_unset=True,
    description="Read the latest 100 audit entries for a resource UUID, newest first. "
    "An unknown resource returns an empty list; original transcripts are not included.",
)
async def audit(resource_id: uuid.UUID, db: DB) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.resource_id == resource_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(100)
        )
    ).all()
    return [
        {"action": r.action, "created_at": r.created_at.isoformat(), "details": r.details}
        for r in rows
    ]
