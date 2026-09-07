"""Seed synthetic scenarios through the same public REST API used by the console."""

import argparse
import json
import os
import time
import urllib.request
import uuid
from typing import Any

SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "A": [
        {"sequence": 1, "text": "최근 일주일 동안 잠드는 데 한 시간 정도 걸렸어요."},
        {"sequence": 2, "text": "아침에는 피곤해서 업무에 집중하기 어렵습니다."},
    ],
    "B": [
        {"sequence": 10, "text": "보통 네 시간 정도 자요."},
        {"sequence": 11, "speaker": "interviewer", "text": "평소 수면시간을 다시 확인해 주세요."},
        {"sequence": 12, "text": "제가 잘못 말했어요. 보통 여섯 시간 정도 잡니다.", "corrects": 10},
    ],
    "C": [{"sequence": 1, "text": "최근에는 퇴근 후 집에서 쉬고 있어요."}],
    "D": [
        {"sequence": 1, "text": "홍길동 씨는 서울 영등포구에 거주합니다."},
        {"sequence": 2, "text": "연락처는 010-1234-5678, 이메일은 demo@example.com입니다."},
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()

    def request(path: str, body: dict[str, Any] | None = None) -> Any:
        req = urllib.request.Request(
            args.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": os.getenv("API_KEY", ""),
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)

    def wait(job_id: str) -> Any:
        for _ in range(120):
            job = request(f"/api/jobs/{job_id}")
            if job["state"] == "FAILED":
                raise RuntimeError(f"Job failed: {job['error_code']}")
            if job["state"] == "REVIEW_REQUIRED":
                return request(f"/api/jobs/{job_id}/result")
            time.sleep(0.25)
        raise TimeoutError("Worker did not finish in 30 seconds")

    request("/ready")
    for code, utterances in SCENARIOS.items():
        session = request("/api/sessions", {"title": f"Scenario {code} - Synthetic interview"})
        sid = session["id"]
        request(f"/api/sessions/{sid}/transcript", {"utterances": utterances})
        job = request(
            f"/api/sessions/{sid}/inferences",
            {
                "prompt_version": "note-v1" if code == "B" else "note-v2",
                "model_version": "mock-unsupported" if code == "C" else "mock-v1",
            },
        )
        result = wait(job["id"])
        print(
            json.dumps(
                {
                    "scenario": code,
                    "session_id": sid,
                    "run_id": result["run_id"],
                    "validation": result["validation_summary"],
                }
            )
        )
        if code == "A":
            reviewed = request(
                f"/api/runs/{result['run_id']}/reviews",
                {
                    "action": "approve",
                    "reviewer": "demo-seed",
                    "reason": "Synthetic evidence reviewed",
                },
            )
            assert reviewed["status"] == "APPROVED"
        if code == "B":
            replay = request(f"/api/runs/{result['run_id']}/replay", {"prompt_version": "note-v2"})
            replay_result = wait(replay["id"])
            comparison = request(
                f"/api/comparisons?original={result['run_id']}&replay={replay_result['run_id']}"
            )
            assert comparison["replay"]["stale_count"] == 0
            print(json.dumps({"comparison": comparison}))
    print("Verified A-D through REST and background worker. Console: " + args.url)


if __name__ == "__main__":
    main()
