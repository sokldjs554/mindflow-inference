import pytest
from pydantic import ValidationError

from app.providers import DeterministicEvidenceValidator, DeterministicRedactor, MockLLMProvider
from app.schemas import NoteOutput, Statement, TranscriptInput


@pytest.mark.parametrize(
    "claim,refs,source,stale,status",
    [
        ("보통 여섯 시간 잡니다.", [1], "보통 여섯 시간 잡니다.", None, "SUPPORTED"),
        ("운동합니다.", [], "보통 여섯 시간 잡니다.", None, "UNSUPPORTED"),
        ("운동합니다.", [999], "운동합니다.", None, "UNSUPPORTED"),
        ("보통 네 시간 잡니다.", [1], "보통 여섯 시간 잡니다.", None, "CONTRADICTED"),
        ("보통 네 시간 잡니다.", [1], "보통 네 시간 잡니다.", 2, "STALE_EVIDENCE"),
        ("여섯 시간", [1], "보통 여섯 시간 잡니다.", None, "PARTIALLY_SUPPORTED"),
        ("기분이 좋습니다.", [1], "보통 여섯 시간 잡니다.", None, "UNSUPPORTED"),
    ],
)
def test_evidence(claim, refs, source, stale, status):
    value, _ = DeterministicEvidenceValidator().validate(
        Statement(text=claim, evidence=refs),
        [{"sequence": 1, "text": source, "superseded_by": stale}],
    )
    assert value == status


def test_redaction_boundary():
    raw = "홍길동 씨는 서울 영등포구에 거주합니다. 010-1234-5678 demo@example.com"
    result = DeterministicRedactor().redact([{"sequence": 1, "text": raw}])
    assert result.transcript[0]["text"] == (
        "[PERSON_01] 씨는 [LOCATION_01]에 거주합니다. [PHONE_01] [EMAIL_01]"
    )
    assert result.mapping["[PERSON_01]"] == "홍길동"
    assert "홍길동" not in str(result.transcript)


@pytest.mark.parametrize(
    "output",
    [
        {"subjective": [], "objective": [], "plan": []},
        {"subjective": [{"text": "x", "evidence": ["1"]}], "objective": [], "plan": []},
        {"subjective": [{"text": "x", "evidence": [True]}], "objective": [], "plan": []},
        {"subjective": [{"text": "x", "evidence": [1], "extra": 1}], "objective": [], "plan": []},
        {"subjective": []},
    ],
)
def test_strict_schema(output):
    with pytest.raises(ValidationError):
        NoteOutput.model_validate(output)


def test_duplicate_sequences_rejected():
    with pytest.raises(ValidationError):
        TranscriptInput(utterances=[{"sequence": 1, "text": "a"}, {"sequence": 1, "text": "b"}])


async def test_prompt_changes_correction_behavior():
    data = [
        {"sequence": 1, "speaker": "client", "text": "a", "superseded_by": 2},
        {"sequence": 2, "speaker": "client", "text": "b", "superseded_by": None},
    ]
    provider = MockLLMProvider()
    assert len((await provider.generate(data, "historical", "mock-v1"))["subjective"]) == 2
    assert len((await provider.generate(data, "ACTIVE_ONLY", "mock-v1"))["subjective"]) == 1
