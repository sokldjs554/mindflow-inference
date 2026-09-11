"""Synthetic rules, persisted clinical review gate and immutable replay contracts."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.clinical import generate, validate_item
from app.db import SessionFactory
from app.models import InferenceRun
from app.schemas import ClinicalItem, ClinicalSupport


def source(text, sequence=1, **kwargs):
    return {
        "sequence": sequence,
        "speaker": "client",
        "text": text,
        "superseded_by": None,
        **kwargs,
    }


def test_grounded_signals_and_context_questions():
    output = generate(
        [
            source("최근 일주일 동안 잠드는 데 한 시간 정도 걸렸어요."),
            source("아침에는 피곤해서 업무에 집중하기 어렵습니다.", 2),
        ]
    )
    items = {i.id: i for i in output.items}
    assert items["sleep"].evidence == [1]
    assert items["fatigue"].evidence == [2]
    assert items["function"].evidence == [2]
    assert all(i.validation == "SUPPORTED" for i in output.items)
    assert "duration-question" not in items
    assert "impact-question" not in items
    assert "sleep-question" in items
    assert output.review_status == "REQUIRES_CLINICIAN_REVIEW"


@pytest.mark.parametrize(
    "text",
    [
        "피로가 심하지 않아요.",
        "예전에는 우울해요라고 말했어요.",
        "친구는 불안해요.",
        "잠을 못 자나요?",
    ],
)
def test_negated_historical_other_person_and_questions_not_signals(text):
    assert not generate([source(text)]).items


def test_superseded_and_interviewer_excluded():
    assert not generate(
        [source("우울해요", superseded_by=2), source("우울해요", 2, speaker="interviewer")]
    ).items


def test_candidate_is_pattern_only_and_unknown_information_is_explicit():
    output = generate([source("우울해요. 계속 피곤합니다.")])
    assert "pattern" in [i.id for i in output.items]
    assert "duration-question" in [i.id for i in output.items]
    assert "impact-question" in [i.id for i in output.items]
    assert all(i.clinical_status == "REQUIRES_CLINICIAN_REVIEW" for i in output.items)


@pytest.mark.parametrize("refs", [[], [999], [1]])
def test_unlicensed_candidate_not_validated_even_with_real_reference(refs):
    item = ClinicalItem(id="pattern", kind="condition_candidate", text="확정된 질환", evidence=refs)
    result = validate_item(item, [source("우울해요. 계속 피곤합니다.")])
    assert result.validation == "UNSUPPORTED"
    assert result.clinical_status == "REQUIRES_CLINICIAN_REVIEW"


@pytest.mark.parametrize("status", ["FINAL_DIAGNOSIS", "FINAL_TREATMENT", "PRESCRIPTION"])
def test_final_clinical_state_rejected_by_schema(status):
    with pytest.raises(ValidationError):
        ClinicalSupport(rule_version="test", items=[], review_status=status)
    with pytest.raises(ValidationError):
        ClinicalItem(
            id="test", kind="recommendation", text="test", evidence=[], clinical_status=status
        )


async def test_clinical_review_audit_replay_and_correction(client):
    from test_integration import finish, submit

    sid, jid = await submit(client, [{"sequence": 1, "text": "우울해요. 계속 피곤합니다."}])
    first = await finish(client, jid)
    clinical = first["clinical_support"]
    assert clinical["run_id"] == first["run_id"]
    assert clinical["timestamp"] == first["trace"]["completed_at"]
    assert clinical["rule_version"] == "clinical-rules-v1"
    assert clinical["review_status"] == "REQUIRES_CLINICIAN_REVIEW"
    url = f"/api/runs/{first['run_id']}/reviews"
    body = {"action": "approve", "reviewer": "demo-clinician", "reason": "Reviewed candidates"}
    reviewed = await client.post(url, json=body)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["clinical_support"]["review_status"] == "APPROVED"
    assert reviewed.json()["clinical_support"]["reviewer"] == "demo-clinician"
    assert all(
        i["clinical_status"] == "REQUIRES_CLINICIAN_REVIEW"
        for i in reviewed.json()["clinical_support"]["items"]
    )
    audits = (await client.get(f"/api/audit/{first['note_id']}")).json()
    assert audits[0]["action"] == "approve"
    assert audits[0]["details"]["reviewer"] == "demo-clinician"
    await client.post(
        f"/api/sessions/{sid}/transcript",
        json={
            "utterances": [{"sequence": 2, "text": "정정합니다. 피로가 없습니다.", "corrects": 1}]
        },
    )
    changed = (await client.get(f"/api/runs/{first['run_id']}")).json()["clinical_support"]
    assert changed["review_status"] == "REQUIRES_CLINICIAN_REVIEW"
    assert changed["reviewer"] is None
    assert all(i["current_validation"] == "STALE_EVIDENCE" for i in changed["items"])
    assert all(i["validation"] == "SUPPORTED" for i in changed["items"])
    assert (await client.post(url, json=body)).status_code == 409
    replay_job = await client.post(
        f"/api/runs/{first['run_id']}/replay",
        json={},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    replay = await finish(client, replay_job.json()["id"])
    assert replay["trace"]["input_hash"] == first["trace"]["input_hash"]
    stored = replay["trace"]["provider_metadata"]["clinical_support"]
    assert stored == first["trace"]["provider_metadata"]["clinical_support"]
    assert replay["clinical_support"]["review_status"] == "REQUIRES_CLINICIAN_REVIEW"


async def test_unsupported_clinical_item_blocks_shared_gate(client):
    from test_integration import finish, submit

    _, jid = await submit(client)
    output = await finish(client, jid)
    unsupported = validate_item(
        ClinicalItem(
            id="unknown", kind="condition_candidate", text="Unknown candidate", evidence=[]
        ),
        output["source"],
    )
    async with SessionFactory() as db, db.begin():
        run = await db.scalar(
            select(InferenceRun).where(InferenceRun.id == uuid.UUID(output["run_id"]))
        )
        metadata = dict(run.provider_metadata)
        metadata["clinical_support"] = ClinicalSupport(
            rule_version="test", items=[unsupported]
        ).model_dump(mode="json")
        run.provider_metadata = metadata
    url = f"/api/runs/{output['run_id']}/reviews"
    body = {"action": "approve", "reviewer": "demo", "reason": "Synthetic test"}
    response = await client.post(url, json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "UNSAFE_APPROVAL"
    body["action"] = "reject"
    rejected = await client.post(url, json=body)
    assert rejected.json()["clinical_support"]["review_status"] == "REJECTED"
    audits = (await client.get(f"/api/audit/{output['note_id']}")).json()
    assert audits[0]["action"] == "reject"


async def test_legacy_run_not_retroactively_generated(client):
    from test_integration import finish, submit

    _, jid = await submit(client)
    output = await finish(client, jid)
    async with SessionFactory() as db, db.begin():
        run = await db.get(InferenceRun, uuid.UUID(output["run_id"]))
        run.provider_metadata = {
            k: v for k, v in run.provider_metadata.items() if k != "clinical_support"
        }
    legacy = (await client.get(f"/api/runs/{output['run_id']}")).json()
    assert legacy["clinical_support"] is None
