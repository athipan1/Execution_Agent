from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, get_broker_state_reconciliation_service


class FakeBrokerStateReconciliationService:
    def __init__(self):
        self.calls = []

    async def reconcile(self, account_id=1, *, push_to_database=True):
        self.calls.append({"account_id": account_id, "push_to_database": push_to_database})
        return {
            "ok": True,
            "broker_state": {"account_id": account_id},
            "database_sync": {"status": "success" if push_to_database else "skipped"},
            "reconciled_at": "2026-06-26T00:00:00+00:00",
        }


def test_broker_reconcile_alias_pushes_snapshot_to_database():
    fake_service = FakeBrokerStateReconciliationService()
    app.dependency_overrides[get_broker_state_reconciliation_service] = lambda: fake_service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/broker/reconcile?account_id=1&push_to_database=true",
                headers={"X-API-KEY": settings.API_KEY},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["ok"] is True
    assert body["data"]["database_sync"]["status"] == "success"
    assert fake_service.calls == [{"account_id": "1", "push_to_database": True}]


def test_broker_reconcile_state_uses_same_reconcile_service():
    fake_service = FakeBrokerStateReconciliationService()
    app.dependency_overrides[get_broker_state_reconciliation_service] = lambda: fake_service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/broker/reconcile-state?account_id=7&push_to_database=false",
                headers={"X-API-KEY": settings.API_KEY},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["database_sync"]["status"] == "skipped"
    assert fake_service.calls == [{"account_id": "7", "push_to_database": False}]
