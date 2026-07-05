import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models import StandardAgentResponse


REQUIRED_STANDARD_RESPONSE_FIELDS = {
    "status",
    "agent_type",
    "version",
    "schema_version",
    "timestamp",
    "correlation_id",
    "data",
    "metadata",
    "error",
    "confidence_score",
}


def assert_standard_response(payload):
    assert REQUIRED_STANDARD_RESPONSE_FIELDS.issubset(payload.keys())
    assert payload["agent_type"] == "execution-agent"
    assert payload["version"] == "1.0.0"
    assert payload["schema_version"] == "1.0"


def test_standard_agent_response_has_contract_defaults():
    response = StandardAgentResponse(
        status="success",
        data={"ok": True},
    )

    payload = response.model_dump(mode="json")

    assert REQUIRED_STANDARD_RESPONSE_FIELDS.issubset(payload.keys())
    assert payload["agent_type"] == "execution-agent"
    assert payload["version"] == "1.0.0"
    assert payload["schema_version"] == "1.0"
    assert payload["correlation_id"] is None
    assert payload["metadata"] == {}
    assert payload["confidence_score"] is None
    assert payload["timestamp"]


def test_standard_agent_response_rejects_invalid_schema_version():
    with pytest.raises(ValidationError):
        StandardAgentResponse(
            status="success",
            schema_version="v1",
            data={},
        )


def test_version_endpoint_uses_standard_contract_without_api_key():
    client = TestClient(app)
    response = client.get("/version")

    assert response.status_code == 200
    payload = response.json()
    assert_standard_response(payload)
    assert payload["data"]["api_contract"] == "multi-agent-trading-api-contract"
    assert payload["data"]["schema_version"] == "1.0"
    assert payload["data"]["service_version"] == "1.3.1"


def test_ready_endpoint_uses_standard_contract_without_api_key():
    client = TestClient(app)
    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert_standard_response(payload)
    assert "ready" in payload["data"]
    assert "broker_mode" in payload["data"]
    assert "live_guard_ok" in payload["data"]
    assert payload["metadata"]["contract_source"] == "execution-agent-runtime-contract"


def test_protected_execution_endpoint_still_requires_api_key():
    client = TestClient(app)
    response = client.post("/execute", json={})

    assert response.status_code == 401
    payload = response.json()
    assert_standard_response(payload)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "HTTP_401"
