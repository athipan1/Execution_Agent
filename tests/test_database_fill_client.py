from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.db_client import HttpDatabaseClient
from app.models import FillPayload, OrderSide


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {
            "status": "success",
            "data": {
                "fill_id": 1,
                "realized_pnl": 19.0,
            },
        }


@pytest.mark.asyncio
async def test_http_database_client_posts_fill_to_account_endpoint():
    posted = {}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            posted["url"] = url
            posted["json"] = json
            posted["headers"] = headers
            return FakeResponse()

    fill = FillPayload(
        order_id=10,
        trade_id="t-1",
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=2,
        fill_price=110,
        broker_order_id="bo-1",
        filled_at=datetime(2026, 6, 21, 16, 0, tzinfo=timezone.utc),
    )

    with patch("app.db_client.httpx.AsyncClient", FakeAsyncClient), patch("app.db_client.settings.DATABASE_AGENT_API_KEY", "test-key"):
        result = await HttpDatabaseClient("http://database-agent:8004").record_fill(1, fill)

    assert posted["url"] == "http://database-agent:8004/accounts/1/fills"
    assert posted["json"]["order_id"] == 10
    assert posted["json"]["quantity"] == 2
    assert posted["json"]["fill_price"] == 110.0
    assert posted["headers"] == {"X-API-KEY": "test-key"}
    assert result["realized_pnl"] == 19.0
