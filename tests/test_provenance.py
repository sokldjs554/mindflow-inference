import asyncio
import uuid

from sqlalchemy import select
from test_integration import finish, submit

from app.db import SessionFactory
from app.models import AudioAsset, InferenceRun, ModelVersion
from app.providers import (
    DeterministicEvidenceValidator,
    DeterministicRedactor,
    MockLLMProvider,
    MockSTTProvider,
    ProviderMetadata,
)
from app.queue import redis_client
from app.worker import Worker


def metadata(name, version="test-v2"):
    return ProviderMetadata(identifier=name, version=version, mode="custom", deterministic=True)


async def test_injected_provider_snapshots_and_stt_retry(client):
    class LLM(MockLLMProvider):
        metadata = metadata("replacement-llm")
        calls = 0

        async def generate(self, transcript, prompt, model):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError
            return await super().generate(transcript, prompt, model)

    class STT(MockSTTProvider):
        metadata = metadata("original-transcriber")
        calls = 0

        async def transcribe(self, audio):
            self.calls += 1
            return await super().transcribe(audio)

    class ReplacementSTT(MockSTTProvider):
        metadata = metadata("must-not-be-recorded")

        async def transcribe(self, audio):
            raise AssertionError("Persisted audio snapshot must not be transcribed again")

    class Redactor(DeterministicRedactor):
        metadata = metadata("replacement-redactor")

    class Validator(DeterministicEvidenceValidator):
        metadata = metadata("replacement-validator")

    sid = (await client.post("/api/sessions", json={"title": "Synthetic provider trace"})).json()[
        "id"
    ]
    async with SessionFactory() as db, db.begin():
        asset = AudioAsset(
            session_id=uuid.UUID(sid),
            content_type="audio/wav",
            sha256="0" * 64,
            data=b"synthetic adapter fixture",
        )
        db.add(asset)
        await db.flush()
        aid = str(asset.id)
    job = await client.post(
        f"/api/sessions/{sid}/inferences",
        json={"audio_id": aid},
        headers={"Idempotency-Key": "provenance-audio"},
    )
    jid = job.json()["id"]
    redis = redis_client()
    llm, stt = LLM(), STT()
    try:
        await Worker(redis, llm, stt, Redactor(), Validator()).tick(block_ms=10)
        assert (await client.get(f"/api/jobs/{jid}")).json()["state"] == "QUEUED"
        await asyncio.sleep(0.02)
        await Worker(redis, llm, ReplacementSTT(), Redactor(), Validator()).tick(block_ms=10)
        result = (await client.get(f"/api/jobs/{jid}/result")).json()
        trace = result["trace"]
        assert trace["retry_count"] == 1
        assert trace["model_identifier"] == "replacement-llm"
        providers = trace["provider_metadata"]["providers"]
        assert providers == {
            "llm": LLM.metadata.model_dump(),
            "stt": STT.metadata.model_dump(),
            "redaction": Redactor.metadata.model_dump(),
            "evidence_validation": Validator.metadata.model_dump(),
        }
        assert stt.calls == 1
        assert trace["provider_metadata"]["mode"] == "custom"
        assert trace["provider_metadata"]["audio_fixture"] is False
        # Changing the implementation later cannot change persisted run provenance.
        llm.metadata = metadata("later-implementation")
        again = (await client.get(f"/api/runs/{result['run_id']}")).json()
        assert again["trace"] == trace
        replay = await client.post(
            f"/api/runs/{result['run_id']}/replay",
            json={"model_version": "mock-unsupported"},
            headers={"Idempotency-Key": "provider-replay"},
        )
        replayed = await finish(client, replay.json()["id"])
        assert replayed["trace"]["provider_metadata"]["providers"]["stt"] is None
        assert replayed["trace"]["model_identifier"] == MockLLMProvider.metadata.identifier
        assert replayed["trace"]["model_version"] == "mock-unsupported"
        assert replayed["validation_summary"]["unsupported_count"] == 1
        assert replayed["trace"]["input_hash"] == trace["input_hash"]
    finally:
        await redis.aclose()


async def test_legacy_result_uses_catalog_not_hardcoded_identifier(client):
    _, jid = await submit(client)
    result = await finish(client, jid)
    assert result["trace"]["provider_metadata"]["providers"]["stt"] is None
    async with SessionFactory() as db, db.begin():
        run = await db.scalar(select(InferenceRun).where(InferenceRun.job_id == uuid.UUID(jid)))
        run.provider_metadata = {"mode": "mock"}
        model = await db.get(ModelVersion, run.model_version)
        model.identifier = "legacy-catalog-identifier"
    legacy = (await client.get(f"/api/runs/{result['run_id']}")).json()
    assert legacy["trace"]["model_identifier"] == "legacy-catalog-identifier"
