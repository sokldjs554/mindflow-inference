import asyncio
import io
import json
import os
import sys
import uuid
import wave
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from websockets.asyncio.client import connect

from app.config import settings
from app.db import SessionFactory
from app.models import AudioAsset, InferenceJob, InferenceRun, JobOutbox, Utterance
from app.providers import MockLLMProvider
from app.queue import dispatch, ensure_group, event_stream, job_stream, redis_client
from app.worker import Worker

pytestmark = pytest.mark.integration


async def submit(client, utterances=None, **options):
    response = await client.post("/api/sessions", json={"title": "Synthetic interview"})
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    response = await client.post(
        f"/api/sessions/{sid}/transcript",
        json={
            "utterances": utterances
            or [{"sequence": 1, "speaker": "client", "text": "보통 여섯 시간 정도 잡니다."}]
        },
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        f"/api/sessions/{sid}/inferences",
        json=options,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 202, response.text
    return sid, response.json()["id"]


async def finish(client, jid, llm=None):
    redis = redis_client()
    try:
        worker = Worker(redis, llm=llm)
        for _ in range(20):
            await worker.tick(block_ms=10)
            state = (await client.get(f"/api/jobs/{jid}")).json()["state"]
            if state in {"FAILED", "REVIEW_REQUIRED", "COMPLETED"}:
                return (await client.get(f"/api/jobs/{jid}/result")).json()
            await asyncio.sleep(0.02)
        pytest.fail("Worker did not reach a terminal inference state")
    finally:
        await redis.aclose()


async def test_normal_pipeline_approval_audit(client):
    sid, jid = await submit(client)
    output = await finish(client, jid)
    assert output["status"] == "REVIEW_REQUIRED"
    assert output["statements"][0]["validation"] == "SUPPORTED"
    assert output["trace"]["input_hash"] == output["trace"]["redacted_input_hash"]
    response = await client.post(
        f"/api/runs/{output['run_id']}/reviews",
        json={"action": "approve", "reviewer": "tester", "reason": "Evidence reviewed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "APPROVED"
    audit = (await client.get(f"/api/audit/{output['note_id']}")).json()
    assert audit[0]["action"] == "approve"
    assert len((await client.get(f"/api/sessions/{sid}/runs")).json()["items"]) == 1
    assert (await client.get("/ready")).status_code == 200


@pytest.mark.parametrize(
    "model,status", [("mock-unsupported", "UNSUPPORTED"), ("mock-contradiction", "CONTRADICTED")]
)
async def test_unsafe_approval_blocked_rejection_allowed(client, model, status):
    _, jid = await submit(client, model_version=model)
    output = await finish(client, jid)
    assert status in [s["validation"] for s in output["statements"]]
    url = f"/api/runs/{output['run_id']}/reviews"
    review = {"action": "approve", "reviewer": "tester", "reason": "checked"}
    assert (await client.post(url, json=review)).status_code == 409
    review["action"] = "reject"
    assert (await client.post(url, json=review)).json()["status"] == "REJECTED"
    assert (await client.post(url, json=review)).status_code == 409


async def test_correction_replay_comparison(client):
    sid, jid = await submit(
        client,
        [
            {"sequence": 10, "text": "보통 네 시간 정도 자요."},
            {"sequence": 12, "text": "보통 여섯 시간 정도 잡니다.", "corrects": 10},
        ],
        prompt_version="note-v1",
    )
    a = await finish(client, jid)
    assert a["validation_summary"]["stale_count"] == 1
    response = await client.post(
        f"/api/runs/{a['run_id']}/replay",
        json={"prompt_version": "note-v2"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    b = await finish(client, response.json()["id"])
    comparison = (
        await client.get(
            "/api/comparisons", params={"original": a["run_id"], "replay": b["run_id"]}
        )
    ).json()
    assert comparison["original"]["stale_count"] == 1
    assert comparison["replay"]["stale_count"] == 0
    assert comparison["replay"]["evidence_coverage"] == 1
    assert a["trace"]["input_hash"] == b["trace"]["input_hash"]
    assert (await client.get(f"/api/sessions/{sid}")).json()["utterances"][0]["superseded_by"] == 12


async def test_correction_after_approval_invalidates_and_blocks(client):
    sid, jid = await submit(client)
    result = await finish(client, jid)
    url = f"/api/runs/{result['run_id']}/reviews"
    review = {"action": "approve", "reviewer": "tester", "reason": "checked"}
    assert (await client.post(url, json=review)).status_code == 200
    assert (
        await client.post(
            f"/api/sessions/{sid}/transcript",
            json={
                "utterances": [
                    {"sequence": 2, "text": "정정합니다. 보통 네 시간 잡니다.", "corrects": 1}
                ]
            },
        )
    ).status_code == 201
    current = (await client.get(f"/api/runs/{result['run_id']}")).json()
    assert current["status"] == "REVIEW_REQUIRED"
    assert current["statements"][0]["validation"] == "SUPPORTED"
    assert current["statements"][0]["current_validation"] == "STALE_EVIDENCE"
    assert (await client.post(url, json=review)).status_code == 409


async def test_concurrent_idempotency(client):
    sid, _ = await submit(client)

    async def request(body):
        return await client.post(
            f"/api/sessions/{sid}/inferences", json=body, headers={"Idempotency-Key": "same-key"}
        )

    responses = await asyncio.gather(*(request({}) for _ in range(8)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert (await request({"prompt_version": "note-v1"})).status_code == 409


class FailingProvider:
    metadata = MockLLMProvider.metadata

    def __init__(self, mode):
        self.mode, self.calls = mode, 0

    async def generate(self, transcript, prompt, model):
        self.calls += 1
        if self.mode == "timeout":
            await asyncio.sleep(1)
        if self.mode == "schema":
            return {"invalid": "output"}
        if self.mode == "transient" and self.calls > 1:
            return await MockLLMProvider().generate(transcript, prompt, model)
        raise RuntimeError("SECRET raw provider error must not be persisted")


@pytest.mark.parametrize(
    "mode,code,attempts",
    [
        ("schema", "SCHEMA_INVALID", 1),
        ("timeout", "PROVIDER_TIMEOUT", 3),
        ("failure", "PROVIDER_FAILURE", 3),
        ("transient", None, 2),
    ],
)
async def test_failure_retry_timeout(client, mode, code, attempts):
    _, jid = await submit(client)
    output = await finish(client, jid, FailingProvider(mode))
    job = (await client.get(f"/api/jobs/{jid}")).json()
    assert job["attempts"] == attempts
    assert job["error_code"] == code
    assert "SECRET" not in json.dumps(job)
    if mode == "transient":
        assert output["trace"]["retry_count"] == 1
    else:
        assert job["state"] == "FAILED"


async def test_worker_duplicate_and_pending_recovery(client):
    _, jid = await submit(client)
    redis = redis_client()
    await ensure_group(redis)
    await dispatch(redis)
    messages = await redis.xreadgroup("workers", "dead-consumer", {job_stream(): ">"}, count=1)
    assert messages
    await asyncio.sleep(2.05)
    worker = Worker(redis)
    assert await worker.tick(block_ms=10) == 1
    await redis.xadd(job_stream(), {"job_id": jid})
    await worker.tick(block_ms=10)
    async with SessionFactory() as db:
        assert await db.scalar(select(func.count()).select_from(InferenceRun)) == 1
    assert (await redis.xpending(job_stream(), "workers"))["pending"] == 0
    assert await redis.xlen(event_stream(jid)) >= 6
    await redis.aclose()


async def test_transaction_rollback_and_unique_constraint(client):
    sid, _ = await submit(client)
    response = await client.post(
        f"/api/sessions/{sid}/transcript",
        json={
            "utterances": [
                {"sequence": 2, "text": "should roll back"},
                {"sequence": 3, "text": "bad", "corrects": 999},
            ]
        },
    )
    assert response.status_code == 409
    assert len((await client.get(f"/api/sessions/{sid}")).json()["utterances"]) == 1
    with pytest.raises(IntegrityError):
        async with SessionFactory() as db, db.begin():
            db.add(
                Utterance(session_id=uuid.UUID(sid), sequence=1, speaker="client", text="duplicate")
            )
            await db.flush()


async def test_lost_redis_stream_republished_from_database(client):
    _, jid = await submit(client)
    redis = redis_client()
    try:
        await ensure_group(redis)
        await dispatch(redis)
        await redis.delete(job_stream())
        async with SessionFactory() as db, db.begin():
            outbox = await db.scalar(select(JobOutbox).where(JobOutbox.job_id == uuid.UUID(jid)))
            outbox.published_at = datetime.now(UTC) - timedelta(seconds=31)
        await Worker(redis).tick(block_ms=10)
        assert (await client.get(f"/api/jobs/{jid}")).json()["state"] == "REVIEW_REQUIRED"
        assert await redis.xlen(job_stream()) == 0
        await dispatch(redis)
        assert await redis.xlen(job_stream()) == 0  # Terminal jobs are not republished.
    finally:
        await redis.aclose()


async def test_invalid_stt_output_does_not_persist_transcript(client):
    class InvalidSTT:
        metadata = MockLLMProvider.metadata

        async def transcribe(self, audio):
            return [{"sequence": 1, "speaker": "client", "text": ""}]

    sid = (await client.post("/api/sessions", json={"title": "STT failure"})).json()["id"]
    async with SessionFactory() as db, db.begin():
        asset = AudioAsset(
            session_id=uuid.UUID(sid),
            content_type="audio/wav",
            sha256="0" * 64,
            data=b"synthetic fixture",
        )
        db.add(asset)
        await db.flush()
        aid = str(asset.id)
    response = await client.post(
        f"/api/sessions/{sid}/inferences",
        json={"audio_id": aid},
        headers={"Idempotency-Key": "invalid-stt"},
    )
    jid = response.json()["id"]
    redis = redis_client()
    try:
        await Worker(redis, stt=InvalidSTT()).tick(block_ms=10)
        job = (await client.get(f"/api/jobs/{jid}")).json()
        assert job["state"] == "FAILED" and job["error_code"] == "SCHEMA_INVALID"
        assert (await client.get(f"/api/sessions/{sid}")).json()["utterances"] == []
        async with SessionFactory() as db:
            assert (await db.get(InferenceJob, uuid.UUID(jid))).input_snapshot == []
    finally:
        await redis.aclose()


async def test_rate_and_body_limits(client):
    original_rate, original_body = settings().rate_limit, settings().max_body_bytes
    try:
        settings().rate_limit = 1
        assert (await client.get("/api/sessions")).status_code == 200
        assert (await client.get("/api/sessions")).status_code == 429
        settings().rate_limit = original_rate
        settings().max_body_bytes = 10
        response = await client.post("/api/sessions", json={"title": "too long synthetic title"})
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "BODY_TOO_LARGE"
    finally:
        settings().rate_limit, settings().max_body_bytes = original_rate, original_body


async def test_provider_redaction_boundary(client):
    class Spy(MockLLMProvider):
        async def generate(self, transcript, prompt, model):
            assert "홍길동" not in str(transcript)
            assert "[PERSON_01]" in str(transcript)
            return await super().generate(transcript, prompt, model)

    _, jid = await submit(
        client, [{"sequence": 1, "text": "홍길동 씨는 서울 영등포구에 거주합니다."}]
    )
    output = await finish(client, jid, Spy())
    assert output["trace"]["input_hash"] != output["trace"]["redacted_input_hash"]
    assert "홍길동" not in json.dumps(output, ensure_ascii=False)


async def test_audio_pipeline(client):
    sid = (await client.post("/api/sessions", json={"title": "Synthetic audio"})).json()["id"]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    upload = await client.post(
        f"/api/sessions/{sid}/audio",
        files={"file": ("synthetic.wav", buffer.getvalue(), "audio/wav")},
    )
    assert upload.status_code == 201, upload.text
    job = await client.post(
        f"/api/sessions/{sid}/inferences",
        json={"audio_id": upload.json()["audio_id"]},
        headers={"Idempotency-Key": "audio"},
    )
    output = await finish(client, job.json()["id"])
    assert output["trace"]["provider_metadata"]["audio_fixture"] is True
    assert output["statements"][0]["validation"] == "SUPPORTED"
    bad = await client.post(
        f"/api/sessions/{sid}/audio", files={"file": ("x.wav", b"bad", "audio/wav")}
    )
    assert bad.status_code == 422


async def test_pagination_errors_auth_limits(client):
    for i in range(3):
        await client.post("/api/sessions", json={"title": f"Synthetic {i}"})
    first = (await client.get("/api/sessions?limit=2")).json()
    second = (
        await client.get("/api/sessions", params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert not {x["id"] for x in first["items"]} & {x["id"] for x in second["items"]}
    assert (await client.get("/api/sessions?cursor=bad")).status_code == 422
    invalid = await client.post("/api/sessions", json={"title": "", "secret": "sensitive"})
    assert invalid.status_code == 422 and "sensitive" not in invalid.text
    assert invalid.headers["x-request-id"]
    old = settings().api_key
    settings().api_key = "test-only-key"
    try:
        assert (await client.get("/api/sessions")).status_code == 401
        assert (
            await client.get("/api/sessions", headers={"X-API-Key": "test-only-key"})
        ).status_code == 200
    finally:
        settings().api_key = old
    assert (await client.get("/health")).status_code == 200
    assert "mindflow_http_seconds" in (await client.get("/metrics")).text


async def test_websocket_real_worker_events(client):
    _, jid = await submit(client)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--port",
        "18001",
        "--no-access-log",
        env=os.environ.copy(),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient() as health:
            for _ in range(100):
                try:
                    if (await health.get("http://127.0.0.1:18001/ready")).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Live API failed to start")
        async with connect(f"ws://127.0.0.1:18001/ws/jobs/{jid}") as ws:
            assert json.loads(await ws.recv())["type"] == "snapshot"
            await finish(client, jid)
            states = []
            async with asyncio.timeout(5):
                while "REVIEW_REQUIRED" not in states:
                    event = json.loads(await ws.recv())
                    states.append(event["state"])
            assert "GENERATING" in states and "VALIDATING" in states
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)
