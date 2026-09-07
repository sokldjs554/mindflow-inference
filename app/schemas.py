import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SessionCreate(StrictModel):
    title: str = Field(min_length=1, max_length=120)


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


class Page(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None
