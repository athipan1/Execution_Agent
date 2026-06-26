from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, get_broker_state_reconciliation_service


class FakeBrokerStateReconciliationService:
    def __init__(self):
        self.calls = []

    async def collect_broker_state(self, account_id=1):
        self.calls.append({"account_id": account_id})
        return {
            "source": "execution_agent",
            "account_id": account_id,
            "broker": "ALPACA",
            "paper": True,
            "account": {"cash": "93276.77", "equity": "103685.61"},
            "positions": [{"symbol": "ADBE", "qty": "52"}],
            "open_orders": [{"symbol": "ADBE", "status": "new"}],
            "summary": {"position_count": 1, "open_order_count": 1},
        }


def test_broker_state_route_returns_snapshot_payload():
    fake_service = FakeBrokerStateReconciliationService()
    app.dependency_overrides[get_broker_state_reconciliation_service] = lambda: fake_service
    try:
        with TestClient(app) as client:
            response = client.get(
                "/broker/state?account_id=1",
                headers={"X-API-KEY": settings.API_KEY},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["source"] == "execution_agent"
    assert body["data"]["account_id"] == "1"
    assert body["data"]["summary"] == {"position_count": 1, "open_order_count": 1}
    assert body["data"]["positions"] == [{"symbol": "ADBE", "qty": "52"}]
    assert body["data"]["open_orders"] == [{"symbol": "ADBE", "status": "new"}]
    assert fake_service.calls == [{"account_id": "1"}]
