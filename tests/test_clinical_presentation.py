"""Exercise the browser presentation helper with real synthetic backend output."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.clinical import generate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def presentation():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for browser presentation unit checks")
    sources = [
        {"sequence": 1, "speaker": "client", "text": "우울해요. 계속 피곤합니다."},
        {"sequence": 2, "speaker": "client", "text": "잠을 못 자요."},
    ]
    items = generate(sources).model_dump(mode="json")["items"]
    checks = [{"item": item, "sources": sources} for item in items]
    observed = next(item for item in items if item["id"] == "sleep")
    for state in ["UNSUPPORTED", "STALE_EVIDENCE", "CONTRADICTED", "PARTIALLY_SUPPORTED"]:
        checks.append(
            {"item": {**observed, "id": state, "current_validation": state}, "sources": sources}
        )
    checks.append(
        {"item": {**observed, "id": "absent-reference", "evidence": [999]}, "sources": sources}
    )
    checks.append(
        {"item": {**observed, "id": "empty-reference", "evidence": []}, "sources": sources}
    )
    code = """
const fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync('app/static/clinical.js','utf8');
const present=vm.runInNewContext(script+';clinicalPresentation');
const checks=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(checks.map(({item,sources})=>({
 id:item.id,kind:item.kind,original:item,view:present(item,sources)
}))));
"""
    result = subprocess.run(
        [node, "-e", code],
        input=json.dumps(checks),
        text=True,
        capture_output=True,
        check=True,
        cwd=ROOT,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "kind,status,linked",
    [
        ("observed_signal", "EVIDENCE_MATCHED", True),
        ("condition_candidate", "RULE_DERIVED_CANDIDATE", True),
        ("risk_signal", "RULE_MATCH", True),
        ("missing_information", "NOT_OBSERVED", False),
        ("follow_up_question", "SUGGESTED", False),
        ("next_assessment", "RECOMMENDED_FOR_REVIEW", False),
        ("recommendation", "REVIEW_CANDIDATE", False),
    ],
)
def test_item_semantics_not_misrepresented_as_supported_fact(presentation, kind, status, linked):
    rows = [row for row in presentation if row["kind"] == kind and not row["view"]["warning"]]
    assert rows
    for row in rows:
        assert row["view"]["status"] == status
        assert bool(row["view"]["evidence"]) is linked
        assert "SUPPORTED" not in row["view"]["validationText"]
        # Preserve the validator and contextual references used by the review gate.
        assert row["original"]["current_validation"] == "SUPPORTED"
        assert row["original"]["evidence"]
        if kind in {"condition_candidate", "risk_signal"}:
            assert row["view"]["requiresReview"]
            assert row["view"]["sourceLabel"] == "Rule input"


@pytest.mark.parametrize(
    "state", ["UNSUPPORTED", "STALE_EVIDENCE", "CONTRADICTED", "PARTIALLY_SUPPORTED"]
)
def test_failed_validation_never_presented_as_evidence_matched(presentation, state):
    row = next(row for row in presentation if row["id"] == state)
    assert row["view"]["warning"] == state
    assert row["view"]["status"] == "NOT_MATCHED"


@pytest.mark.parametrize("ident", ["absent-reference", "empty-reference"])
def test_source_button_requires_existing_reference(presentation, ident):
    row = next(row for row in presentation if row["id"] == ident)
    assert row["view"]["evidence"] == []
    assert row["view"]["warning"] == "UNSUPPORTED"
    assert row["view"]["status"] != "EVIDENCE_MATCHED"
