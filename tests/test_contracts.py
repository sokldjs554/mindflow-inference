"""Public OpenAPI contracts and stable JSON response shapes."""

import uuid

from app.main import app
from app.schemas import ErrorEnvelope


def test_every_business_operation_has_concrete_contract():
    schema = app.openapi()
    for path, methods in schema["paths"].items():
        if not path.startswith("/api/"):
            continue
        for operation in methods.values():
            assert len(operation["description"]) > 30
            assert operation["tags"]
            for code in (401, 413, 422, 429, 503):
                response = operation["responses"][str(code)]
                assert response["content"]["application/json"]["schema"]["$ref"].endswith(
                    "/ErrorEnvelope"
                )
            success = next(v for k, v in operation["responses"].items() if k.startswith("2"))
            body = success["content"]["application/json"]["schema"]
            assert "$ref" in body or "$ref" in body.get("items", {})
    assert schema["components"]["schemas"]["AudioView"]["required"] == ["audio_id", "stt_mode"]
    assert "AuditView" in schema["components"]["schemas"]
    for path in ("/api/sessions", "/api/sessions/{session_id}/runs"):
        ref = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        page = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        assert "$ref" in page["properties"]["items"]["items"]


async def test_error_envelope_matches_documented_runtime(client):
    response = await client.get(f"/api/sessions/{uuid.uuid4()}")
    assert response.status_code == 404
    error = ErrorEnvelope.model_validate(response.json())
    assert error.error.code == "SESSION_NOT_FOUND"
    assert error.error.request_id == response.headers["x-request-id"]
    invalid = await client.post("/api/sessions", json={"title": ""})
    assert invalid.status_code == 422
    assert ErrorEnvelope.model_validate(invalid.json()).error.code == "INVALID_REQUEST"


async def test_audit_response_preserves_absent_detail_keys(client):
    from test_integration import finish, submit

    sid, jid = await submit(client)
    await finish(client, jid)
    response = await client.get(f"/api/audit/{sid}")
    assert response.status_code == 200
    assert response.json()[0]["details"] == {"count": 1}
