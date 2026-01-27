import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from app.main import app
from app.config import settings

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

API_KEY = settings.API_KEY
HEADERS = {"X-API-KEY": API_KEY}

def test_execute_trade_success(client: TestClient):
    trade_data = {
        "symbol": "AAPL",
        "quantity": 10,
        "side": "buy",
        "order_type": "market"
    }
    response = client.post("/execute_trade", headers=HEADERS, json=trade_data)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "executed"
    assert result["symbol"] == "AAPL"
    assert result["side"] == "buy"
    assert result["quantity"] == 10
    assert "broker_order_id" in result
    assert "avg_execution_price" in result

def test_execute_trade_fail(client: TestClient):
    trade_data = {
        "symbol": "FAIL.BK",
        "quantity": 10,
        "side": "sell",
        "order_type": "market"
    }
    response = client.post("/execute_trade", headers=HEADERS, json=trade_data)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "failed"
    assert "reason" in result

def test_execute_trade_unauthorized(client: TestClient):
    trade_data = {
        "symbol": "AAPL",
        "quantity": 10,
        "side": "buy"
    }
    # Test without API key
    with pytest.raises(HTTPException) as excinfo:
        client.post("/execute_trade", json=trade_data)
    assert excinfo.value.status_code == 401
