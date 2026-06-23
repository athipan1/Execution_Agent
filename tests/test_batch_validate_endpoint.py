from fastapi.testclient import TestClient

from app.main import app
from app.config import settings


HEADERS = {"X-API-KEY": settings.API_KEY}


def _order(symbol, bucket="core_dividend", trade_id=None):
    return {
        "trade_id": trade_id or f"trade-{symbol}",
        "account_id": 1,
        "symbol": symbol,
        "side": "buy",
        "order_type": "market",
        "price": 100,
        "quantity": 1,
        "final_quantity": 1,
        "risk_approval_id": f"risk-{symbol}",
        "strategy_bucket": bucket,
        "guard_plan": {"stop_loss": 95},
    }


def test_batch_validate_endpoint_allows_safe_batch():
    with TestClient(app) as client:
        response = client.post(
            "/execute/batch/validate",
            headers=HEADERS,
            json=[
                _order("KO", "core_dividend"),
                _order("JNJ", "core_dividend"),
                _order("ACGL", "value_rebound"),
                _order("ADBE", "value_rebound"),
                _order("NEWS1", "news_momentum"),
            ],
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["approved"] is True


def test_batch_validate_endpoint_rejects_duplicate_symbol():
    with TestClient(app) as client:
        response = client.post(
            "/execute/batch/validate",
            headers=HEADERS,
            json=[_order("KO", trade_id="a"), _order("KO", trade_id="b")],
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["approved"] is False
    assert any(error["code"] == "DUPLICATE_SYMBOL_IN_BATCH" for error in payload["data"]["errors"])


def test_batch_validate_endpoint_rejects_multiple_news_orders():
    with TestClient(app) as client:
        response = client.post(
            "/execute/batch/validate",
            headers=HEADERS,
            json=[_order("NEWS1", "news_momentum"), _order("NEWS2", "news_momentum")],
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["approved"] is False
    assert any(error["code"] == "NEWS_MOMENTUM_LIMIT_EXCEEDED" for error in payload["data"]["errors"])
