import pytest
from fastapi.testclient import TestClient
import uuid
from app.main import app
from app.config import settings
from app.models import OrderStatus

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

API_KEY = settings.API_KEY
HEADERS = {"X-API-KEY": API_KEY}

def test_limit_order_missing_price(client):
    """
    Test that a limit order fails if price is missing.
    """
    trade_id = str(uuid.uuid4())
    order_data = {
        "trade_id": trade_id,
        "account_id": "test_acc",
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "limit",
        "quantity": 10,
        # price is missing
    }
    response = client.post("/execute", headers=HEADERS, json=order_data)
    assert response.status_code == 422 # Pydantic validation error
    assert "Price is required for limit orders" in response.text

def test_limit_order_with_price(client):
    """
    Test that a limit order succeeds if price is provided.
    """
    trade_id = str(uuid.uuid4())
    order_data = {
        "trade_id": trade_id,
        "account_id": "test_acc",
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "limit",
        "quantity": 10,
        "price": 150.5
    }
    response = client.post("/execute", headers=HEADERS, json=order_data)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["price"] == 150.5

def test_cancel_order_flow(client):
    """
    Test cancelling an order.
    """
    trade_id = str(uuid.uuid4())
    order_data = {
        "trade_id": trade_id,
        "account_id": "test_acc",
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "market",
        "quantity": 10
    }
    # 1. Create order
    response = client.post("/execute", headers=HEADERS, json=order_data)
    order_id = response.json()["data"]["order_id"]

    # 2. Cancel it
    # Note: Simulator might execute it very fast.
    # But in SimulatorAdapter, we have a sleep(0.1) before execution.
    # So if we are fast enough, it should be in PLACED state.
    response = client.post(f"/execute/{order_id}/cancel", headers=HEADERS)
    # If it was already executed, it might return 400.
    # Let's check.
    if response.status_code == 200:
        assert response.json()["data"]["status"] == OrderStatus.CANCELLED
    else:
        assert response.status_code == 400
        assert "cannot be cancelled" in response.json()["error"]["message"]

def test_auto_refresh_status(client):
    """
    Test that GET /execute/{order_id} refreshes status.
    """
    trade_id = str(uuid.uuid4())
    order_data = {
        "trade_id": trade_id,
        "account_id": "test_acc",
        "symbol": "REFRESH_TEST",
        "side": "buy",
        "order_type": "market",
        "quantity": 100
    }
    # 1. Create order
    response = client.post("/execute", headers=HEADERS, json=order_data)
    order_id = response.json()["data"]["order_id"]

    # Immediately it might be PENDING or PLACED

    # 2. Get order status - this should trigger refresh
    # Simulator.get_order_status returns EXECUTED
    response = client.get(f"/execute/{order_id}", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == OrderStatus.EXECUTED
    assert data["executed_quantity"] == 100
