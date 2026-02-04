import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.models import OrderStatus

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_standard_response_success_format(client):
    trade_data = {
        "trade_id": "standard-test-id",
        "account_id": 1,
        "symbol": "AAPL",
        "quantity": 10,
        "side": "buy",
        "order_type": "market"
    }
    headers = {"X-API-KEY": settings.API_KEY}
    response = client.post("/execute", headers=headers, json=trade_data)

    assert response.status_code == 200
    json_data = response.json()

    # Verify StandardAgentResponse structure
    assert json_data["status"] == "success"
    assert json_data["agent_type"] == "execution"
    assert "timestamp" in json_data
    assert json_data["error"] is None

    # Verify ISO-8601 timestamp
    try:
        datetime.fromisoformat(json_data["timestamp"].replace('Z', '+00:00'))
    except ValueError:
        pytest.fail(f"Timestamp {json_data['timestamp']} is not a valid ISO-8601 string")

    # Verify expanded OrderResponse fields
    order_data = json_data["data"]
    expected_fields = [
        "order_id", "trade_id", "account_id", "symbol", "side",
        "order_type", "price", "quantity", "time_in_force", "status",
        "broker_order_id", "reason", "executed_quantity",
        "avg_execution_price", "executed_at"
    ]
    for field in expected_fields:
        assert field in order_data, f"Field {field} missing in OrderResponse"

def test_standard_response_error_format(client):
    # Trigger an error (unauthorized)
    response = client.post("/execute", headers={"X-API-KEY": "wrong-key"}, json={})

    assert response.status_code == 401
    json_data = response.json()

    # Verify StandardAgentResponse error structure
    assert json_data["status"] == "error"
    assert isinstance(json_data["error"], dict)
    assert "code" in json_data["error"]
    assert "message" in json_data["error"]
    assert json_data["error"]["code"] == "HTTP_401"

    # Verify timestamp in error response
    try:
        datetime.fromisoformat(json_data["timestamp"].replace('Z', '+00:00'))
    except ValueError:
        pytest.fail(f"Timestamp {json_data['timestamp']} is not a valid ISO-8601 string")
