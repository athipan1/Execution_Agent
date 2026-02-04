import pytest
import time
import uuid
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
    trade_id = str(uuid.uuid4())
    trade_data = {
        "trade_id": trade_id,
        "account_id": 1,
        "symbol": "AAPL",
        "quantity": 10,
        "side": "buy",
        "order_type": "market"
    }
    # /execute_trade is an alias for /execute
    response = client.post("/execute_trade", headers=HEADERS, json=trade_data)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    result = data["data"]
    assert result["status"] in ["pending", "executed"]
    order_id = result["order_id"]

    # Poll for completion since it's now handled in background
    for _ in range(10):
        time.sleep(0.1)
        response = client.get(f"/execute/{order_id}", headers=HEADERS)
        if response.json()["data"]["status"] == "executed":
            break
    else:
        pytest.fail("Order did not reach 'executed' status")

def test_execute_trade_fail(client: TestClient):
    trade_id = str(uuid.uuid4())
    trade_data = {
        "trade_id": trade_id,
        "account_id": 1,
        "symbol": "FAIL.BK",
        "quantity": 10,
        "side": "sell",
        "order_type": "market"
    }
    response = client.post("/execute_trade", headers=HEADERS, json=trade_data)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    result = data["data"]
    order_id = result["order_id"]

    # Poll for completion
    for _ in range(10):
        time.sleep(0.1)
        response = client.get(f"/execute/{order_id}", headers=HEADERS)
        if response.json()["data"]["status"] == "failed":
            break
    else:
        pytest.fail("Order did not reach 'failed' status")

def test_execute_trade_unauthorized(client: TestClient):
    trade_data = {
        "trade_id": "some-id",
        "account_id": 1,
        "symbol": "AAPL",
        "quantity": 10,
        "side": "buy",
        "order_type": "market"
    }
    # Test without API key
    response = client.post("/execute_trade", json=trade_data)
    assert response.status_code == 401
    data = response.json()
    assert data["status"] == "error"
    assert data["error"]["code"] == "HTTP_401"
