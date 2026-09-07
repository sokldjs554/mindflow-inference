import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Entity:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Session(Entity, Base):
    __tablename__ = "sessions"
    title: Mapped[str] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (Index("ix_sessions_cursor", "created_at", "id"),)


class Utterance(Entity, Base):
    __tablename__ = "utterances"
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    superseded_by: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("session_id", "sequence"),
        CheckConstraint("sequence > 0"),
        CheckConstraint("superseded_by IS NULL OR superseded_by > sequence"),
    )


class AudioAsset(Entity, Base):
    __tablename__ = "audio_assets"
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    content_type: Mapped[str] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(20), default="1")
    template: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelVersion(Base):
    __tablename__ = "model_versions"
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    identifier: Mapped[str] = mapped_column(String(80))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class InferenceJob(Entity, Base):
    __tablename__ = "inference_jobs"
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    state: Mapped[str] = mapped_column(String(30), default="QUEUED")
    progress: Mapped[int] = mapped_column(default=0)
    input_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    audio_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audio_assets.id"))
    prompt_version: Mapped[str] = mapped_column(ForeignKey("prompt_versions.version"))
    model_version: Mapped[str] = mapped_column(ForeignKey("model_versions.version"))
    replay_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    error_code: Mapped[str | None] = mapped_column(String(60))
    correlation_id: Mapped[str] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("attempts >= 0"),
        CheckConstraint("progress BETWEEN 0 AND 100"),
        CheckConstraint(
            "state IN ('QUEUED','INGESTING','TRANSCRIBING','REDACTING',"
            "'GENERATING','VALIDATING','REVIEW_REQUIRED','COMPLETED','FAILED')"
        ),
    )


class InferenceRun(Entity, Base):
    __tablename__ = "inference_runs"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inference_jobs.id"), unique=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    model_version: Mapped[str] = mapped_column(ForeignKey("model_versions.version"))
    prompt_version: Mapped[str] = mapped_column(ForeignKey("prompt_versions.version"))
    input_hash: Mapped[str] = mapped_column(String(64))
    redacted_input_hash: Mapped[str] = mapped_column(String(64))
    redacted_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[float] = mapped_column()
    retry_count: Mapped[int] = mapped_column()
    validation_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_version: Mapped[str] = mapped_column(String(20), default="1")
    __table_args__ = (Index("ix_runs_session_cursor", "session_id", "created_at", "id"),)


class GeneratedNote(Entity, Base):
    __tablename__ = "generated_notes"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inference_runs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="REVIEW_REQUIRED")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('REVIEW_REQUIRED','APPROVED','REJECTED')"),
        Index(
            "ix_notes_review_cursor",
            "created_at",
            "id",
            postgresql_where=text("status = 'REVIEW_REQUIRED'"),
        ),
    )


class GeneratedSection(Entity, Base):
    __tablename__ = "generated_sections"
    note_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generated_notes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("note_id", "kind", "position"),)


class EvidenceLink(Entity, Base):
    __tablename__ = "evidence_links"
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generated_sections.id"), index=True)
    sequence: Mapped[int] = mapped_column()


class ValidationResult(Entity, Base):
    __tablename__ = "validation_results"
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generated_sections.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(160))


class InferenceEvent(Entity, Base):
    __tablename__ = "inference_events"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inference_jobs.id"), index=True)
    state: Mapped[str] = mapped_column(String(30))
    progress: Mapped[int] = mapped_column()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_events_unpublished", "created_at", postgresql_where=text("published_at IS NULL")),
    )


class JobOutbox(Entity, Base):
    __tablename__ = "job_outbox"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inference_jobs.id"), unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inference_jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewAction(Entity, Base):
    __tablename__ = "review_actions"
    note_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generated_notes.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(String(500))


class AuditEvent(Entity, Base):
    __tablename__ = "audit_events"
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(60))
    correlation_id: Mapped[str] = mapped_column(String(36))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
