"""Providers receive only redacted utterances. Mocks are deliberate, labeled fixtures."""

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Protocol

from app.schemas import EvidenceStatus, Statement

Transcript = list[dict[str, Any]]


class LLMProvider(Protocol):
    async def generate(self, transcript: Transcript, prompt: str, model: str) -> dict[str, Any]: ...


class STTProvider(Protocol):
    async def transcribe(self, audio: bytes) -> Transcript: ...


class RedactionProvider(Protocol):
    def redact(self, transcript: Transcript) -> "RedactedInput": ...


class EvidenceValidationProvider(Protocol):
    def validate(
        self, statement: Statement, transcript: Transcript
    ) -> tuple[EvidenceStatus, str]: ...


@dataclass
class RedactedInput:
    transcript: Transcript
    mapping: dict[str, str]  # Ephemeral only; never logged or persisted with provider input.


class DeterministicRedactor:
    patterns = [
        ("EMAIL", r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
        ("PHONE", r"(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)"),
        ("ID", r"\b\d{6}-[1-4]\d{6}\b"),
        ("PERSON", r"[가-힣]{2,4}(?=\s*씨)"),
        ("LOCATION", r"(?:서울|부산|인천|대구|대전|광주|울산)(?:\s+[가-힣]+[구군])?"),
    ]

    def redact(self, transcript: Transcript) -> RedactedInput:
        mapping: dict[str, str] = {}
        reverse: dict[tuple[str, str], str] = {}
        counts: dict[str, int] = {}
        result = []
        for utterance in transcript:
            value = str(utterance["text"])
            for kind, pattern in self.patterns:

                def replace(match: re.Match[str], kind: str = kind) -> str:
                    key = (kind, match.group())
                    if key not in reverse:
                        counts[kind] = counts.get(kind, 0) + 1
                        token = f"[{kind}_{counts[kind]:02d}]"
                        reverse[key] = token
                        mapping[token] = match.group()
                    return reverse[key]

                value = re.sub(pattern, replace, value)
            result.append({**utterance, "text": value})
        return RedactedInput(result, mapping)


class MockSTTProvider:
    async def transcribe(self, audio: bytes) -> Transcript:
        # Audio validation happens at ingestion. This is a fixed synthetic fixture, not recognition.
        return [
            {
                "sequence": 1,
                "speaker": "client",
                "text": "최근 일주일 동안 잠드는 데 한 시간 정도 걸렸어요.",
                "superseded_by": None,
            }
        ]


class MockLLMProvider:
    async def generate(self, transcript: Transcript, prompt: str, model: str) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        active_only = "ACTIVE_ONLY" in prompt
        statements = [
            {"text": u["text"], "evidence": [u["sequence"]]}
            for u in transcript
            if u["speaker"] == "client" and (not active_only or not u.get("superseded_by"))
        ]
        if model == "mock-unsupported":
            statements.append({"text": "매일 두 시간 동안 운동한다고 보고함.", "evidence": [999]})
        if model == "mock-contradiction":
            statements.append(
                {"text": "보통 아홉 시간 정도 잡니다.", "evidence": [transcript[-1]["sequence"]]}
            )
        return {"subjective": statements, "objective": [], "plan": []}


class DeterministicEvidenceValidator:
    """Conservative exact-match baseline; NOT semantic entailment or clinical validation."""

    @staticmethod
    def hours(value: str) -> set[str]:
        words = {
            "한": "1",
            "두": "2",
            "세": "3",
            "네": "4",
            "다섯": "5",
            "여섯": "6",
            "일곱": "7",
            "여덟": "8",
            "아홉": "9",
        }
        return {
            words.get(v, v)
            for v in re.findall(r"(\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉)\s*시간", value)
        }

    def validate(self, statement: Statement, transcript: Transcript) -> tuple[EvidenceStatus, str]:
        sources = {u["sequence"]: u for u in transcript}
        if not statement.evidence or any(s not in sources for s in statement.evidence):
            return EvidenceStatus.UNSUPPORTED, "Missing or nonexistent source sequence"
        evidence = [sources[s] for s in statement.evidence]
        if any(u.get("superseded_by") for u in evidence):
            return EvidenceStatus.STALE_EVIDENCE, "Evidence references a superseded utterance"
        joined = " ".join(u["text"] for u in evidence)
        claimed, actual = self.hours(statement.text), self.hours(joined)
        if claimed and actual and claimed.isdisjoint(actual):
            return EvidenceStatus.CONTRADICTED, "Explicit duration conflicts with cited source"
        if any(statement.text == u["text"] for u in evidence):
            return EvidenceStatus.SUPPORTED, "Exact source statement match"
        if statement.text in joined:
            return EvidenceStatus.PARTIALLY_SUPPORTED, "Source excerpt requires contextual review"
        return EvidenceStatus.UNSUPPORTED, "No deterministic support; semantic review required"
