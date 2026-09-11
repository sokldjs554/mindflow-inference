"""Synthetic review prompts, not diagnostic criteria or medical triage.

SUPPORTED means the versioned demo rule and its source are grounded, never that
a condition is established. No scores, prescriptions or clinical decisions.
"""

import re
from typing import Literal

from app.providers import DeterministicEvidenceValidator, Transcript
from app.schemas import ClinicalItem, ClinicalSupport, EvidenceStatus, Statement

RULE_VERSION = "clinical-rules-v1"
# Narrow literal Korean demo expressions; unknown wording is deliberately omitted.
SIGNALS = {
    "sleep": (r"잠드는 데 .{0,12}시간|잠을 못|수면이 악화|잠이 줄", "수면 어려움 관련 표현"),
    "fatigue": (r"피곤해서|피로가 심|심한 피로|계속 피곤", "피로 관련 표현"),
    "function": (r"업무에 집중하기 어렵|일상생활이 어렵|일을 못", "기능 어려움 관련 표현"),
    "mood": (r"우울해요|우울합니다|불안해요|불안합니다", "불안·우울 관련 표현"),
}
NEGATED = re.compile(r"않|아니|없|예전|과거|가정|만약|[?？]|라고|친구|가족")


def proposals(transcript: Transcript) -> list[ClinicalItem]:
    active = [u for u in transcript if u["speaker"] == "client" and not u.get("superseded_by")]
    items: list[ClinicalItem] = []
    matches: dict[str, list[int]] = {}

    def add(
        key: str,
        kind: Literal[
            "observed_signal",
            "condition_candidate",
            "risk_signal",
            "missing_information",
            "follow_up_question",
            "next_assessment",
            "recommendation",
        ],
        text: str,
        refs: list[int],
    ) -> None:
        items.append(ClinicalItem(id=key, kind=kind, text=text, evidence=sorted(set(refs))))

    for key, (pattern, label) in SIGNALS.items():
        refs = [
            u["sequence"]
            for u in active
            if re.search(pattern, u["text"]) and not NEGATED.search(u["text"])
        ]
        if refs:
            matches[key] = refs
            add(key, "observed_signal", label, refs)
            add("risk-" + key, "risk_signal", label + " — 의료진 확인 후보 (위험 등급 아님)", refs)
    refs = sorted({s for values in matches.values() for s in values})
    if not refs:
        return items
    if "mood" in matches and ("sleep" in matches or "fatigue" in matches):
        add(
            "pattern",
            "condition_candidate",
            "기분·수면/피로 표현의 동반 패턴 후보 — 질환 진단 기준을 평가하지 않음",
            refs,
        )
    relevant = " ".join(u["text"] for u in active if u["sequence"] in refs)
    if not re.search(r"일주일|\d+\s*(일|주|개월)|지난주|한 달", relevant):
        add("duration", "missing_information", "관련 발언에서 증상 지속 기간을 확인하지 못함", refs)
        add(
            "duration-question",
            "follow_up_question",
            "이러한 어려움은 언제부터 지속되었나요?",
            refs,
        )
    if "function" not in matches:
        add("impact", "missing_information", "규칙으로 일상 기능 영향을 확인하지 못함", refs)
        add("impact-question", "follow_up_question", "업무나 일상생활에 어떤 영향이 있나요?", refs)
    if "sleep" in matches:
        add(
            "sleep-question",
            "follow_up_question",
            "최근 수면 패턴은 어떻게 변했나요?",
            matches["sleep"],
        )
    add(
        "assessment",
        "next_assessment",
        "표시된 발언의 기간·맥락·기능 영향을 의료진이 추가 확인할 후보",
        refs,
    )
    add(
        "recommendation",
        "recommendation",
        "근거 발언과 질문 후보를 의료진이 검토하여 추가 평가 필요 여부를 판단",
        refs,
    )
    return items


def validate_item(item: ClinicalItem, transcript: Transcript) -> ClinicalItem:
    expected = {candidate.id: candidate for candidate in proposals(transcript)}.get(item.id)
    status = EvidenceStatus.UNSUPPORTED
    reason = "No matching demo rule or evidence; not an established clinical finding"
    sources = {u["sequence"]: u for u in transcript}
    if (
        expected
        and (item.kind, item.text, item.evidence)
        == (expected.kind, expected.text, expected.evidence)
        and item.evidence
    ):
        checks = [
            DeterministicEvidenceValidator().validate(
                Statement(text=sources[s]["text"], evidence=[s]), transcript
            )[0]
            for s in item.evidence
        ]
        status = next(
            (s for s in checks if s != EvidenceStatus.SUPPORTED), EvidenceStatus.SUPPORTED
        )
        reason = "Versioned demo rule matched exact active source; clinical meaning requires review"
    return item.model_copy(
        update={"validation": status, "current_validation": status, "reason": reason}
    )


def generate(transcript: Transcript) -> ClinicalSupport:
    return ClinicalSupport(
        rule_version=RULE_VERSION,
        items=[validate_item(item, transcript) for item in proposals(transcript)],
    )
