"""Public demo policy using the unchanged browser Scenario A-E fixtures."""

import hashlib
from typing import Any

from app.config import settings
from app.errors import DomainError
from app.schemas import ReviewInput

SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "E": [
        {"sequence": 1, "speaker": "client", "text": "우울해요. 계속 피곤합니다."},
        {
            "sequence": 2,
            "speaker": "interviewer",
            "text": "지속 기간과 일상생활 영향을 확인해 주세요.",
        },
    ],
    "A": [
        {
            "sequence": 1,
            "speaker": "client",
            "text": "최근 일주일 동안 잠드는 데 한 시간 정도 걸렸어요.",
        },
        {
            "sequence": 2,
            "speaker": "client",
            "text": "아침에는 피곤해서 업무에 집중하기 어렵습니다.",
        },
    ],
    "B": [
        {"sequence": 10, "speaker": "client", "text": "보통 네 시간 정도 자요."},
        {
            "sequence": 11,
            "speaker": "interviewer",
            "text": "평소 수면시간을 다시 확인해 주시겠어요?",
        },
        {
            "sequence": 12,
            "speaker": "client",
            "text": "제가 잘못 말했어요. 보통 여섯 시간 정도 잡니다.",
            "corrects": 10,
        },
    ],
    "C": [{"sequence": 1, "speaker": "client", "text": "최근에는 퇴근 후 집에서 쉬고 있어요."}],
    "D": [
        {"sequence": 1, "speaker": "client", "text": "홍길동 씨는 서울 영등포구에 거주합니다."},
        {
            "sequence": 2,
            "speaker": "client",
            "text": "연락처는 010-1234-5678이고 이메일은 demo@example.com입니다.",
        },
    ],
}
DEMO_REASON = "가상 기록의 근거와 검토 제안을 확인했습니다."


def forbidden() -> None:
    raise DomainError(
        "PUBLIC_DEMO_RESTRICTED", "공개 데모에서는 가상 예시 기록만 사용할 수 있습니다.", 403
    )


def block_free_input() -> None:
    if settings().public_demo_mode:
        forbidden()


def safe_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest() if settings().public_demo_mode else value


def safe_review(body: ReviewInput) -> ReviewInput:
    if settings().public_demo_mode:
        return ReviewInput(action=body.action, reviewer="demo-reviewer", reason=DEMO_REASON)
    return body


def validate_snapshot(rows: list[dict[str, Any]]) -> None:
    if not settings().public_demo_mode:
        return
    allowed = []
    for fixture in SCENARIOS.values():
        allowed.append(
            [
                {
                    "sequence": u["sequence"],
                    "speaker": u["speaker"],
                    "text": u["text"],
                    "superseded_by": next(
                        (v["sequence"] for v in fixture if v.get("corrects") == u["sequence"]), None
                    ),
                }
                for u in fixture
            ]
        )
    if rows not in allowed:
        forbidden()
