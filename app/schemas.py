import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SessionCreate(StrictModel):
    title: str = Field(min_length=1, max_length=120)


class DemoConfig(BaseModel):
    public_demo_mode: bool


class UtteranceInput(StrictModel):
    sequence: int = Field(gt=0, strict=True)
    speaker: Literal["client", "interviewer"] = "client"
    text: str = Field(min_length=1, max_length=4000)
    corrects: int | None = Field(default=None, gt=0)


class TranscriptInput(StrictModel):
    utterances: list[UtteranceInput] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_sequences(self) -> "TranscriptInput":
        seqs = [u.sequence for u in self.utterances]
        if len(set(seqs)) != len(seqs) or seqs != sorted(seqs):
            raise ValueError("Sequences must be unique and increasing")
        if sum(len(u.text) for u in self.utterances) > 100_000:
            raise ValueError("Transcript exceeds 100000 characters")
        return self


class InferenceCreate(StrictModel):
    prompt_version: Literal["note-v1", "note-v2"] = "note-v2"
    model_version: Literal["mock-v1", "mock-unsupported", "mock-contradiction"] = "mock-v1"
    audio_id: uuid.UUID | None = None


class ReviewInput(StrictModel):
    action: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    reason: str = Field(min_length=1, max_length=500)


class EvidenceStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    STALE_EVIDENCE = "STALE_EVIDENCE"


class Statement(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=4000)
    evidence: list[Annotated[int, Field(gt=0, strict=True)]] = Field(max_length=200)


class NoteOutput(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subjective: list[Statement] = Field(max_length=200)
    objective: list[Statement] = Field(max_length=200)
    plan: list[Statement] = Field(max_length=200)

    @model_validator(mode="after")
    def nonempty(self) -> "NoteOutput":
        if not (self.subjective or self.objective or self.plan):
            raise ValueError("At least one statement is required")
        return self


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    created_at: datetime


class JobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    session_id: uuid.UUID
    state: str
    progress: int
    attempts: int
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


class SourceUtterance(BaseModel):
    sequence: int
    speaker: Literal["client", "interviewer"]
    text: str
    superseded_by: int | None


class SessionDetail(SessionView):
    utterances: list[SourceUtterance]


class TranscriptView(BaseModel):
    utterances: list[SourceUtterance]


class ValidatedStatement(BaseModel):
    id: uuid.UUID
    kind: Literal["subjective", "objective", "plan"]
    text: str
    evidence: list[int]
    validation: EvidenceStatus
    current_validation: EvidenceStatus
    reason: str


class ValidationSummary(BaseModel):
    evidence_coverage: float
    unsupported_count: int
    contradiction_count: int
    stale_count: int
    partial_count: int
    schema_validity: bool
    statement_count: int


class InferenceTrace(BaseModel):
    model_identifier: str
    model_version: str
    prompt_version: str
    schema_version: str
    input_hash: str
    redacted_input_hash: str
    started_at: datetime
    completed_at: datetime
    latency_ms: float
    retry_count: int
    provider_metadata: dict[str, Any]


class EvidenceView(BaseModel):
    statements: list[ValidatedStatement]
    source: list[SourceUtterance]


class ClinicalItem(StrictModel):
    id: str
    kind: Literal[
        "observed_signal",
        "condition_candidate",
        "risk_signal",
        "missing_information",
        "follow_up_question",
        "next_assessment",
        "recommendation",
    ]
    text: str
    evidence: list[Annotated[int, Field(gt=0, strict=True)]]
    validation: EvidenceStatus = EvidenceStatus.UNSUPPORTED
    current_validation: EvidenceStatus = EvidenceStatus.UNSUPPORTED
    reason: str = "Not validated"
    clinical_status: Literal["REQUIRES_CLINICIAN_REVIEW"] = "REQUIRES_CLINICIAN_REVIEW"


class ClinicalSupport(StrictModel):
    rule_version: str
    provider: Literal["deterministic-clinical-rules"] = "deterministic-clinical-rules"
    model_identifier: Literal["clinical-demo-v1"] = "clinical-demo-v1"
    validation_scope: str = "Rule applicability and source grounding only; not clinical validity"
    items: list[ClinicalItem]
    run_id: uuid.UUID | None = None
    timestamp: datetime | None = None
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    review_status: Literal["REQUIRES_CLINICIAN_REVIEW", "APPROVED", "REJECTED"] = (
        "REQUIRES_CLINICIAN_REVIEW"
    )
    stages: list[Literal["GENERATED", "EVIDENCE_VALIDATED", "REQUIRES_CLINICIAN_REVIEW"]] = [
        "GENERATED",
        "EVIDENCE_VALIDATED",
        "REQUIRES_CLINICIAN_REVIEW",
    ]


class ResultView(EvidenceView):
    run_id: uuid.UUID
    job_id: uuid.UUID
    note_id: uuid.UUID
    status: Literal["REVIEW_REQUIRED", "APPROVED", "REJECTED"]
    trace: InferenceTrace
    validation_summary: ValidationSummary
    clinical_support: ClinicalSupport | None = None


class ComparisonMetrics(ValidationSummary):
    latency_ms: float
    prompt_version: str
    model_version: str


class ComparisonView(BaseModel):
    original: ComparisonMetrics
    replay: ComparisonMetrics
    delta: dict[str, float]


class RunListItem(BaseModel):
    id: uuid.UUID
    created_at: datetime
    job_id: uuid.UUID
    prompt_version: str
    model_version: str


class AudioView(BaseModel):
    audio_id: uuid.UUID
    stt_mode: str


class AuditDetails(BaseModel):
    count: int | None = None
    replay: bool | None = None
    reviewer: str | None = None


class AuditView(BaseModel):
    action: str
    created_at: datetime
    details: AuditDetails


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail
