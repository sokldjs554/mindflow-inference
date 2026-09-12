import json
import subprocess
import uuid

import pytest
from sqlalchemy import func, select
from test_integration import finish, submit

from app.config import Settings, settings
from app.db import SessionFactory
from app.demo import DEMO_REASON, SCENARIOS
from app.models import AudioAsset, IdempotencyKey, ReviewAction, Session, Utterance


@pytest.fixture
def public_mode(monkeypatch):
    monkeypatch.setattr(settings(), "public_demo_mode", True)


def test_default_and_environment(monkeypatch):
    monkeypatch.delenv("PUBLIC_DEMO_MODE", raising=False)
    assert Settings(_env_file=None).public_demo_mode is False
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    assert Settings(_env_file=None).public_demo_mode is True


def test_fixtures_match_existing_browser():
    script = """
const fs=require('fs');const s=fs.readFileSync('app/static/app.js','utf8');
eval(s.slice(s.indexOf('const scenarios'),s.indexOf('let publicDemo'))+
';console.log(JSON.stringify(scenarios))');
"""
    assert json.loads(subprocess.check_output(["node", "-e", script])) == SCENARIOS


async def scenario(client, code):
    response = await client.post(f"/api/demo/scenarios/{code}")
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    response = await client.post(
        f"/api/sessions/{sid}/inferences",
        json={
            "prompt_version": "note-v1" if code == "B" else "note-v2",
            "model_version": "mock-unsupported" if code == "C" else "mock-v1",
        },
        headers={"Idempotency-Key": "private-user-text"},
    )
    assert response.status_code == 202, response.text
    return sid, await finish(client, response.json()["id"])


@pytest.mark.parametrize("code", list("ABCDE"))
async def test_public_scenarios_review_replay(client, public_mode, code):
    sid, result = await scenario(client, code)
    assert result["clinical_support"]["provider"] == "deterministic-clinical-rules"
    if code == "E":
        assert result["clinical_support"]["items"]
    assert result["trace"]["model_identifier"]
    rid = result["run_id"]
    assert (await client.get(f"/api/runs/{rid}/evidence")).status_code == 200
    review = {"action": "approve", "reviewer": "private-alias", "reason": "private reason"}
    response = await client.post(f"/api/runs/{rid}/reviews", json=review)
    if code in {"B", "C"}:
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "UNSAFE_APPROVAL"
        if code == "B":
            assert result["validation_summary"]["stale_count"] == 1
        review["action"] = "reject"
        response = await client.post(f"/api/runs/{rid}/reviews", json=review)
    assert response.status_code == 200, response.text
    assert response.json()["clinical_support"]["reviewer"] == "demo-reviewer"
    audit = (await client.get(f"/api/audit/{result['note_id']}")).json()
    assert audit[0]["details"]["reviewer"] == "demo-reviewer"
    replay = await client.post(
        f"/api/runs/{rid}/replay",
        json={"prompt_version": "note-v2"},
        headers={"Idempotency-Key": "another-private-key"},
    )
    assert replay.status_code == 202, replay.text
    repeated = await finish(client, replay.json()["id"])
    assert repeated["trace"]["input_hash"] == result["trace"]["input_hash"]
    assert repeated["validation_summary"]["stale_count"] == 0
    comparison = await client.get(
        "/api/comparisons", params={"original": rid, "replay": repeated["run_id"]}
    )
    assert comparison.status_code == 200
    async with SessionFactory() as db:
        stored = (await db.scalars(select(ReviewAction))).all()
        assert [(r.reviewer, r.reason) for r in stored] == [("demo-reviewer", DEMO_REASON)]
        keys = (await db.scalars(select(IdempotencyKey.key))).all()
        assert "private-user-text" not in keys and "another-private-key" not in keys


async def test_public_direct_rest_cannot_store_text(client, public_mode):
    sid = (await client.post("/api/demo/scenarios/E")).json()["id"]
    requests = [
        ("/api/sessions", {"title": "private title"}),
        (f"/api/sessions/{sid}/transcript", {"utterances": [{"sequence": 3, "text": "private"}]}),
        (
            f"/api/sessions/{sid}/transcript",
            {"utterances": [{"sequence": 3, "text": "private", "corrects": 1}]},
        ),
        ("/api/demo/scenarios/E", {"title": "private", "utterances": SCENARIOS["E"]}),
        ("/api/demo/scenarios/INVALID", {}),
        (f"/api/sessions/{sid}/inferences", {"audio_id": str(uuid.uuid4())}),
    ]
    for path, body in requests:
        response = await client.post(path, json=body, headers={"Idempotency-Key": "private"})
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "PUBLIC_DEMO_RESTRICTED"
    response = await client.post(
        f"/api/sessions/{sid}/audio", files={"file": ("private.wav", b"private", "audio/wav")}
    )
    assert response.status_code == 403
    async with SessionFactory() as db:
        assert await db.scalar(select(func.count()).select_from(Session)) == 1
        assert await db.scalar(select(func.count()).select_from(Utterance)) == 2
        assert await db.scalar(select(func.count()).select_from(AudioAsset)) == 0


async def test_local_free_input_and_old_record_blocked(client, monkeypatch):
    monkeypatch.setattr(settings(), "public_demo_mode", False)
    sid, jid = await submit(client)
    result = await finish(client, jid)
    monkeypatch.setattr(settings(), "public_demo_mode", True)
    for path in [f"/api/sessions/{sid}/inferences", f"/api/runs/{result['run_id']}/replay"]:
        response = await client.post(path, json={}, headers={"Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 403
