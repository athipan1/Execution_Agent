from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import httpx

from app.adapters.alpaca import AlpacaAdapter
from app.config import settings
from app.models import Order


@pytest.mark.parametrize('field', ['timestamp','next_open','next_close'])
def test_invalid_timezone_minutes_are_not_normalized(field, open_clock):
    from app.services.execution_session import validate_execution_clock
    clock = dict(open_clock)
    parsed = datetime.fromisoformat(clock[field].replace('Z','+00:00'))
    clock[field] = (parsed+timedelta(minutes=99)).strftime('%Y-%m-%dT%H:%M:%S')+'+00:99'
    assert validate_execution_clock(clock) == 'session_unverified'


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["closed", "stale", "missing", "naive", "invalid_boolean", "boundary", "outage"])
async def test_clock_failure_never_submits_order(case, open_clock, respx_mock, monkeypatch):
    monkeypatch.setattr(settings, "TRADING_MODE", "PAPER")
    monkeypatch.setattr(settings, "ALPACA_API_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setattr(settings, "ALPACA_API_KEY_ID", "fixture-key")
    monkeypatch.setattr(settings, "ALPACA_SECRET_KEY", "fixture-secret")
    now = datetime.now(timezone.utc)
    clock = dict(open_clock)
    if case == "closed":
        clock.update(is_open=False, next_open=(now+timedelta(hours=1)).isoformat(),
                     next_close=(now+timedelta(hours=7)).isoformat())
    elif case == "stale": clock["timestamp"] = (now-timedelta(seconds=31)).isoformat()
    elif case == "missing": clock = {}
    elif case == "naive": clock["timestamp"] = now.replace(tzinfo=None).isoformat()
    elif case == "invalid_boolean": clock["is_open"] = "true"
    elif case == "boundary": clock["next_close"] = now.isoformat()
    respx_mock.get(settings.ALPACA_API_URL + "/v2/clock").respond(
        503 if case == "outage" else 200, json=clock)
    submit = respx_mock.post(settings.ALPACA_API_URL + "/v2/orders").respond(200, json={})
    order = Order(order_id=1, trade_id="clock-test", account_id=1, symbol="AAPL",
        side="buy", order_type="market", quantity=1, time_in_force="GTC",
        guard_plan={"symbol":"AAPL", "side":"sell", "quantity":1,
                    "trigger_price":90, "take_profit_price":110})
    callback = AsyncMock()
    adapter = AlpacaAdapter()
    await adapter.place_order(order, callback)
    await adapter._client.aclose()
    assert not submit.called
    assert callback.await_args.args[0]["status"] == "failed"
    assert ("market_closed" if case == "closed" else "session_unverified") in callback.await_args.args[0]["reason"]
