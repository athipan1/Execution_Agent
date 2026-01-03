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

def test_idempotency_with_client_order_id(client: TestClient):
    """
    Verifies that duplicate requests using the same client_order_id
    do not create a new order and instead return the existing one.
    """
    client_order_id = str(uuid.uuid4())
    order_data = {**BASE_ORDER, "client_order_id": client_order_id}

    response1 = client.post("/orders", headers=HEADERS, json=order_data)
    assert response1.status_code == 200
    order1 = response1.json()
    assert "order_id" in order1

    response2 = client.post("/orders", headers=HEADERS, json=order_data)
    assert response2.status_code == 200
    order2 = response2.json()

    assert order1["order_id"] == order2["order_id"]
    assert order1["client_order_id"] == order2["client_order_id"]
    assert order2["status"] != "pending"

def test_idempotency_with_header(client: TestClient):
    """
    Verifies that the Idempotency-Key header works as intended.
    """
    idempotency_key = str(uuid.uuid4())
    custom_headers = {**HEADERS, "Idempotency-Key": idempotency_key}

    order_data1 = {**BASE_ORDER, "client_order_id": str(uuid.uuid4())}
    order_data2 = {**BASE_ORDER, "client_order_id": str(uuid.uuid4())}

    response1 = client.post("/orders", headers=custom_headers, json=order_data1)
    assert response1.status_code == 200
    order1 = response1.json()
    assert order1["client_order_id"] == idempotency_key

    response2 = client.post("/orders", headers=custom_headers, json=order_data2)
    assert response2.status_code == 200
    order2 = response2.json()

    assert order1["order_id"] == order2["order_id"]
    assert order2["client_order_id"] == idempotency_key
