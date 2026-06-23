import uuid

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


HEADERS = {"X-API-KEY": settings.API_KEY}


def _payload(symbol, bucket="core_dividend", trade_id=None):
    return {
        "trade_id": trade_id or str(uuid.uuid4()),
        "account_id": 1,
        "symbol": symbol,
        "side": "buy",
        "order_type": "market",
        "quantity": 1,
        "risk_approval_id": "risk-test-approval",
        "final_quantity": 1,
        "strategy_bucket": bucket,
        "guard_plan": {"symbol": symbol, "side": "sell", "quantity": 1, "trigger_price": 95},
    }


def test_batch_endpoint_rejects_duplicate_symbol():
    with TestClient(app) as client:
        response = client.post(
            "/execute/batch",
            headers=HEADERS,
            json=[_payload("KO", trade_id="a"), _payload("KO", trade_id="b")],
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["approved"] is False
    assert data["created"] == []
    assert any(error["code"] == "DUPLICATE_SYMBOL_IN_BATCH" for error in data["validation"]["errors"])


def test_batch_endpoint_rejects_multiple_news_momentum():
    with TestClient(app) as client:
        response = client.post(
            "/execute/batch",
            headers=HEADERS,
            json=[_payload("NEWS1", "news_momentum"), _payload("NEWS2", "news_momentum")],
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["approved"] is False
    assert data["created"] == []
    assert any(error["code"] == "NEWS_MOMENTUM_LIMIT_EXCEEDED" for error in data["validation"]["errors"])
