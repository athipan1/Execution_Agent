import pytest
from fastapi.testclient import TestClient
import uuid

from app.main import app
from app.config import settings

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

API_KEY = settings.API_KEY
HEADERS = {"X-API-KEY": API_KEY}
BASE_ORDER = {
    "account_id": 1,
    "symbol": "IDEMPOTENT.BK",
    "side": "buy",
    "order_type": "market",
    "quantity": 50,
}

def test_idempotency_with_trade_id(client: TestClient, approved_order):
    """
    Verifies that duplicate requests using the same trade_id
    do not create a new order and instead return the existing one.
    """
    trade_id = str(uuid.uuid4())
    order_data = approved_order({**BASE_ORDER, "trade_id": trade_id})

    response1 = client.post("/execute", headers=HEADERS, json=order_data)
    assert response1.status_code == 202
    order1 = response1.json()["data"]["order"]
    assert "order_id" in order1

    response2 = client.post("/execute", headers=HEADERS, json=order_data)
    assert response2.status_code == 202
    order2 = response2.json()["data"]["order"]

    assert order1["order_id"] == order2["order_id"]
    assert order1["trade_id"] == order2["trade_id"]
    assert order2["status"] == "pending"
    assert response1.json()["data"]["execution_job"]["job_id"] == response2.json()["data"]["execution_job"]["job_id"]

def test_idempotency_with_header(client: TestClient, approved_order):
    """
    Verifies that the Idempotency-Key header works as intended.
    """
    idempotency_key = str(uuid.uuid4())
    custom_headers = {**HEADERS, "Idempotency-Key": idempotency_key}

    order_data1 = approved_order({**BASE_ORDER, "trade_id": str(uuid.uuid4())})
    order_data2 = {**order_data1, "trade_id": str(uuid.uuid4())}

    response1 = client.post("/execute", headers=custom_headers, json=order_data1)
    assert response1.status_code == 202
    order1 = response1.json()["data"]["order"]
    assert order1["trade_id"] == idempotency_key

    response2 = client.post("/execute", headers=custom_headers, json=order_data2)
    assert response2.status_code == 202
    order2 = response2.json()["data"]["order"]

    assert order1["order_id"] == order2["order_id"]
    assert order2["trade_id"] == idempotency_key
