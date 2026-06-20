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
    "symbol": "AOT.BK",
    "side": "buy",
    "order_type": "market",
    "quantity": 100,
    "risk_approval_id": "risk-test-approval",
    "final_quantity": 100,
    "guard_plan": {
        "symbol": "AOT.BK",
        "side": "sell",
        "quantity": 100,
        "trigger_price": 95,
        "time_in_force": "GTC",
    },
}


def test_health_check(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "healthy"


def test_rejects_order_without_risk_gate(client: TestClient):
    trade_id = str(uuid.uuid4())
    order_data = {
        "account_id": 1,
        "symbol": "AOT.BK",
        "side": "buy",
        "order_type": "market",
        "quantity": 100,
        "trade_id": trade_id,
    }

    response = client.post("/execute", headers=HEADERS, json=order_data)
    assert response.status_code == 422


def test_rejects_quantity_that_differs_from_final_quantity(client: TestClient):
    trade_id = str(uuid.uuid4())
    order_data = {**BASE_ORDER, "trade_id": trade_id, "quantity": 200, "final_quantity": 100}

    response = client.post("/execute", headers=HEADERS, json=order_data)
    assert response.status_code == 422


def test_create_order_enqueues_job_and_worker_executes(client: TestClient):
    trade_id = str(uuid.uuid4())
    order_data = {**BASE_ORDER, "trade_id": trade_id}

    response = client.post("/execute", headers=HEADERS, json=order_data)
    assert response.status_code == 202

    payload = response.json()["data"]
    order_id = payload["order"]["order_id"]
    job_id = payload["execution_job"]["job_id"]
    assert payload["order"]["status"] == "pending"
    assert payload["execution_job"]["status"] == "queued"

    job_response = client.get(f"/jobs/{job_id}", headers=HEADERS)
    assert job_response.status_code == 200
    assert job_response.json()["data"]["order_id"] == order_id

    worker_response = client.post("/jobs/process-next", headers=HEADERS)
    assert worker_response.status_code == 200
    assert worker_response.json()["data"]["status"] == "succeeded"

    response = client.get(f"/execute/{order_id}", headers=HEADERS)
    assert response.status_code == 200
    current_order = response.json()["data"]
    assert current_order["status"] == "executed"
    assert current_order["executed_quantity"] == 100
    assert current_order["executed_at"] is not None


def test_failed_order_job_records_failure(client: TestClient):
    trade_id = str(uuid.uuid4())
    order_data = {**BASE_ORDER, "trade_id": trade_id, "symbol": "FAIL.BK", "guard_plan": {**BASE_ORDER["guard_plan"], "symbol": "FAIL.BK"}}

    response = client.post("/execute", headers=HEADERS, json=order_data)
    assert response.status_code == 202
    payload = response.json()["data"]
    order_id = payload["order"]["order_id"]

    worker_response = client.post("/jobs/process-next", headers=HEADERS)
    assert worker_response.status_code == 200
    assert worker_response.json()["data"]["status"] in {"queued", "failed"}

    response = client.get(f"/execute/{order_id}", headers=HEADERS)
    assert response.status_code == 200
    failed_order = response.json()["data"]
    assert failed_order["status"] == "failed"


def test_unauthorized_access(client: TestClient):
    response = client.post("/execute", headers={}, json=BASE_ORDER)
    assert response.status_code == 401
    data = response.json()
    assert data["status"] == "error"
    assert data["error"]["code"] == "HTTP_401"

    response = client.post("/execute", headers={"X-API-KEY": "wrong-key"}, json=BASE_ORDER)
    assert response.status_code == 401
    data = response.json()
    assert data["status"] == "error"
    assert data["error"]["code"] == "HTTP_401"
