import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DomainError
from app.models import (
    AuditEvent,
    EvidenceLink,
    GeneratedNote,
    GeneratedSection,
    IdempotencyKey,
    InferenceEvent,
    InferenceJob,
    InferenceRun,
    JobOutbox,
    ModelVersion,
    PromptVersion,
    ReviewAction,
    Session,
    Utterance,
    ValidationResult,
)
from app.schemas import InferenceCreate, ReviewInput, TranscriptInput


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


async def catalog(db: AsyncSession) -> None:
    for version, template in [
        (
            "note-v1",
            "Extract client statements with sequence evidence. Include historical statements.",
        ),
        (
            "note-v2",
            "ACTIVE_ONLY: Extract active client statements with sequence evidence. "
            "No diagnosis or inferred treatment. Return subjective, objective, plan arrays.",
        ),
    ]:
        await db.execute(
            insert(PromptVersion)
            .values(
                version=version,
                name="Evidence note",
                schema_version="1",
                template=template,
                active=True,
            )
            .on_conflict_do_nothing()
        )
    for version in ["mock-v1", "mock-unsupported", "mock-contradiction"]:
        await db.execute(
            insert(ModelVersion)
            .values(version=version, identifier="deterministic-mock", metadata_={"synthetic": True})
            .on_conflict_do_nothing()
        )


async def require_session(db: AsyncSession, session_id: uuid.UUID, lock: bool = False) -> Session:
    query = select(Session).where(Session.id == session_id)
    if lock:
        query = query.with_for_update()
    value = await db.scalar(query)
    if value is None:
        raise DomainError("SESSION_NOT_FOUND", "Session does not exist", 404)
    return value


async def transcript(db: AsyncSession, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(
            select(Utterance).where(Utterance.session_id == session_id).order_by(Utterance.sequence)
        )
    ).all()
    return [
        {
            "sequence": r.sequence,
            "speaker": r.speaker,
            "text": r.text,
            "superseded_by": r.superseded_by,
        }
        for r in rows
    ]


async def append_transcript(
    db: AsyncSession, session_id: uuid.UUID, data: TranscriptInput, correlation: str
) -> None:
    session = await require_session(db, session_id, lock=True)
    existing = {
        u.sequence: u
        for u in (
            await db.scalars(select(Utterance).where(Utterance.session_id == session_id))
        ).all()
    }
    if len(existing) + len(data.utterances) > 200:
        raise DomainError("TRANSCRIPT_LIMIT", "Session supports at most 200 utterances", 413)
    if (
        sum(len(u.text) for u in existing.values()) + sum(len(u.text) for u in data.utterances)
        > 100_000
    ):
        raise DomainError("TRANSCRIPT_LIMIT", "Session exceeds 100000 characters", 413)
    last = max(existing, default=0)
    for item in data.utterances:
        if item.sequence <= last:
            raise DomainError("SEQUENCE_CONFLICT", "Append-only sequences must increase")
        if item.corrects is not None:
            old = existing.get(item.corrects)
            if old is None or old.superseded_by or item.corrects >= item.sequence:
                raise DomainError(
                    "INVALID_CORRECTION", "Correction must target an active prior utterance"
                )
            old.superseded_by = item.sequence
        row = Utterance(
            session_id=session_id, sequence=item.sequence, speaker=item.speaker, text=item.text
        )
        db.add(row)
        existing[item.sequence] = row
        last = item.sequence
    session.updated_at = datetime.now(UTC)
    # Corrections invalidate approvals conservatively; historical output stays immutable.
    if any(u.corrects is not None for u in data.utterances):
        notes = (
            await db.scalars(
                select(GeneratedNote)
                .join(InferenceRun)
                .where(InferenceRun.session_id == session_id, GeneratedNote.status == "APPROVED")
                .with_for_update(of=GeneratedNote)
            )
        ).all()
        for note in notes:
            note.status = "REVIEW_REQUIRED"
            db.add(
                AuditEvent(
                    resource_id=note.id,
                    action="approval_invalidated_by_correction",
                    correlation_id=correlation,
                    details={},
                )
            )
    db.add(
        AuditEvent(
            resource_id=session_id,
            action="transcript_appended",
            correlation_id=correlation,
            details={"count": len(data.utterances)},
        )
    )


async def create_job(
    db: AsyncSession,
    session_id: uuid.UUID,
    data: InferenceCreate,
    key: str,
    correlation: str,
    replay: InferenceRun | None = None,
) -> InferenceJob:
    # Session row serializes transcript snapshots and concurrent submissions for this session.
    await require_session(db, session_id, lock=True)
    request_hash = digest(
        {
            "session_id": session_id,
            **data.model_dump(mode="json"),
            "replay_of": replay.id if replay else None,
        }
    )
    previous = await db.get(IdempotencyKey, key)
    if previous:
        if previous.request_hash != request_hash:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT", "Key was already used for a different request"
            )
        job = await db.get(InferenceJob, previous.job_id)
        assert job is not None
        return job
    if replay:
        original = await db.get(InferenceJob, replay.job_id)
        assert original is not None
        snapshot = original.input_snapshot
        audio_id = None
    else:
        snapshot = await transcript(db, session_id)
        audio_id = data.audio_id
        if audio_id:
            from app.models import AudioAsset

            audio = await db.get(AudioAsset, audio_id)
            if audio is None or audio.session_id != session_id:
                raise DomainError("AUDIO_NOT_FOUND", "Audio does not belong to session", 404)
            if snapshot:
                raise DomainError("INPUT_CONFLICT", "Audio inference requires an empty transcript")
        elif not snapshot:
            raise DomainError("EMPTY_INPUT", "Submit transcript or audio first", 422)
    await catalog(db)
    job = InferenceJob(
        id=uuid.uuid4(),
        session_id=session_id,
        input_snapshot=snapshot,
        audio_id=audio_id,
        prompt_version=data.prompt_version,
        model_version=data.model_version,
        replay_of=replay.id if replay else None,
        correlation_id=correlation,
    )
    db.add(job)
    await db.flush()
    # Global PK handles the remaining cross-session idempotency race atomically.
    db.add(IdempotencyKey(key=key, request_hash=request_hash, job_id=job.id))
    db.add(JobOutbox(job_id=job.id))
    db.add(InferenceEvent(job_id=job.id, state="QUEUED", progress=0))
    db.add(
        AuditEvent(
            resource_id=job.id,
            action="inference_requested",
            correlation_id=correlation,
            details={"replay": replay is not None},
        )
    )
    await db.flush()
    return job


async def result(db: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    run = await db.get(InferenceRun, run_id)
    if run is None:
        raise DomainError("RUN_NOT_FOUND", "Inference run does not exist", 404)
    note = await db.scalar(select(GeneratedNote).where(GeneratedNote.run_id == run.id))
    assert note is not None
    sections = (
        await db.execute(
            select(GeneratedSection, ValidationResult)
            .join(ValidationResult, ValidationResult.section_id == GeneratedSection.id)
            .where(GeneratedSection.note_id == note.id)
            .order_by(GeneratedSection.kind, GeneratedSection.position)
        )
    ).all()
    links = (
        await db.scalars(
            select(EvidenceLink).join(GeneratedSection).where(GeneratedSection.note_id == note.id)
        )
    ).all()
    evidence: dict[uuid.UUID, list[int]] = {}
    for link in links:
        evidence.setdefault(link.section_id, []).append(link.sequence)
    live = {u["sequence"]: u for u in await transcript(db, run.session_id)}
    statements = []
    for section, validation in sections:
        seqs = sorted(evidence.get(section.id, []))
        stale_now = any(live.get(s, {}).get("superseded_by") for s in seqs)
        statements.append(
            {
                "id": str(section.id),
                "kind": section.kind,
                "text": section.text,
                "evidence": seqs,
                "validation": validation.status,
                "current_validation": "STALE_EVIDENCE" if stale_now else validation.status,
                "reason": validation.reason,
            }
        )
    return {
        "run_id": str(run.id),
        "job_id": str(run.job_id),
        "note_id": str(note.id),
        "status": note.status,
        "statements": statements,
        "trace": {
            "model_identifier": "deterministic-mock",
            "model_version": run.model_version,
            "prompt_version": run.prompt_version,
            "schema_version": run.schema_version,
            "input_hash": run.input_hash,
            "redacted_input_hash": run.redacted_input_hash,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat(),
            "latency_ms": run.latency_ms,
            "retry_count": run.retry_count,
            "provider_metadata": run.provider_metadata,
        },
        "validation_summary": run.validation_summary,
        "source": run.redacted_snapshot,
    }


async def review(
    db: AsyncSession, run_id: uuid.UUID, data: ReviewInput, correlation: str
) -> dict[str, Any]:
    run = await db.get(InferenceRun, run_id)
    if run is None:
        raise DomainError("RUN_NOT_FOUND", "Inference run does not exist", 404)
    await require_session(db, run.session_id, lock=True)
    note = await db.scalar(
        select(GeneratedNote).where(GeneratedNote.run_id == run_id).with_for_update()
    )
    assert note is not None
    if note.status != "REVIEW_REQUIRED":
        raise DomainError("REVIEW_CONFLICT", "Note has already been reviewed")
    output = await result(db, run_id)
    if data.action == "approve" and any(
        s["current_validation"] != "SUPPORTED" for s in output["statements"]
    ):
        raise DomainError("UNSAFE_APPROVAL", "All statements must be supported by active evidence")
    note.status = "APPROVED" if data.action == "approve" else "REJECTED"
    note.reviewed_at = datetime.now(UTC)
    db.add(ReviewAction(note_id=note.id, **data.model_dump()))
    db.add(
        AuditEvent(
            resource_id=note.id,
            action=data.action,
            correlation_id=correlation,
            details={"reviewer": data.reviewer},
        )
    )
    job = await db.get(InferenceJob, run.job_id)
    assert job is not None
    job.state = "COMPLETED"
    db.add(InferenceEvent(job_id=job.id, state="COMPLETED", progress=100))
    output["status"] = note.status
    return output


def encode_cursor(created: datetime, ident: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created.isoformat()}|{ident}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        timestamp, ident = base64.urlsafe_b64decode(cursor).decode().split("|")
        value = datetime.fromisoformat(timestamp)
        if value.tzinfo is None:
            raise ValueError
        return value, uuid.UUID(ident)
    except (ValueError, UnicodeError) as exc:
        raise DomainError("INVALID_CURSOR", "Invalid pagination cursor", 422) from exc


async def list_page(
    db: AsyncSession,
    model: type[Session] | type[InferenceRun],
    limit: int,
    cursor: str | None,
    session_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    query = select(model).order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
    if session_id is not None:
        query = query.where(InferenceRun.session_id == session_id)
    if cursor:
        query = query.where(tuple_(model.created_at, model.id) < decode_cursor(cursor))
    rows = cast(list[Session | InferenceRun], list((await db.scalars(query)).all()))
    items = [
        {
            "id": str(r.id),
            "created_at": r.created_at.isoformat(),
            **(
                {"title": r.title}
                if isinstance(r, Session)
                else {
                    "job_id": str(r.job_id),
                    "prompt_version": r.prompt_version,
                    "model_version": r.model_version,
                }
            ),
        }
        for r in rows[:limit]
    ]
    return {
        "items": items,
        "next_cursor": encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id)
        if len(rows) > limit
        else None,
    }


async def compare(db: AsyncSession, left: uuid.UUID, right: uuid.UUID) -> dict[str, Any]:
    a, b = await db.get(InferenceRun, left), await db.get(InferenceRun, right)
    if a is None or b is None:
        raise DomainError("RUN_NOT_FOUND", "Both runs must exist", 404)
    if a.session_id != b.session_id or a.input_hash != b.input_hash:
        raise DomainError("INCOMPARABLE_RUNS", "Comparison requires the same immutable input")

    def metrics(run: InferenceRun) -> dict[str, Any]:
        return {
            **run.validation_summary,
            "latency_ms": run.latency_ms,
            "prompt_version": run.prompt_version,
            "model_version": run.model_version,
        }

    return {
        "original": metrics(a),
        "replay": metrics(b),
        "delta": {
            k: b.validation_summary[k] - a.validation_summary[k]
            for k in ["evidence_coverage", "unsupported_count", "contradiction_count"]
        },
    }
