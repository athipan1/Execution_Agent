import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.db_client import HttpDatabaseClient, InMemoryDatabaseClient
from app.models import (
    CreateOrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskApproval,
    RiskApprovalStatus,
)
from app.services.execution_service import ExecutionService
from app.services.strategy_bucket_contract import (
    StrategyBucketPersistenceError,
    normalize_strategy_bucket,
    resolved_strategy_bucket_for_report,
)


class NoopBroker:
    async def place_order(self, order, update_callback):
        return None

    async def get_order_status(self, broker_order_id: str):
        return {"status": "error", "message": "not implemented"}

    async def cancel_order(self, broker_order_id: str):
        return {"status": "cancelled"}

    async def execute(self, trade_order):
        return None

    async def get_account(self):
        return {}

    async def get_positions(self):
        return []

    async def get_open_orders(self):
        return []

    async def check_connection(self):
        return True


class DroppingBucketDatabase(InMemoryDatabaseClient):
    async def create_order(self, order_data: CreateOrderRequest):
        order = await super().create_order(order_data)
        dropped = order.model_copy(update={"strategy_bucket": "unassigned"})
        self._orders_by_trade_id[dropped.trade_id] = dropped
        self._orders_by_order_id[dropped.order_id] = dropped
        return dropped.model_copy()


def approval(symbol="CINF", quantity=5):
    return RiskApproval(
        approval_id="risk-bucket-test",
        account_id=1,
        symbol=symbol,
        side=OrderSide.BUY,
        approved_quantity=quantity,
        status=RiskApprovalStatus.APPROVED,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def order_request(**overrides):
    data = {
        "trade_id": "trade-cinf-value-rebound",
        "account_id": 1,
        "symbol": "CINF",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": 5,
        "strategy_bucket": "value_rebound",
        "risk_approval_id": "risk-bucket-test",
        "final_quantity": 5,
        "guard_plan": {
            "symbol": "CINF",
            "side": "sell",
            "quantity": 5,
            "trigger_price": 110,
            "time_in_force": "GTC",
        },
    }
    data.update(overrides)
    return CreateOrderRequest(**data)


def test_quality_growth_is_a_supported_execution_bucket():
    request = order_request(
        symbol="ACGL",
        trade_id="trade-acgl-quality-growth",
        strategy_bucket="quality_growth",
    )

    assert request.strategy_bucket == "quality_growth"
    assert normalize_strategy_bucket("quality_growth") == "quality_growth"


@pytest.mark.asyncio
async def test_matching_persisted_bucket_allows_order_and_consumes_approval():
    db = InMemoryDatabaseClient()
    db.seed_risk_approval(approval())
    service = ExecutionService(db, NoopBroker())

    order = await service.create_order(order_request())
    used = await db.get_risk_approval("risk-bucket-test")

    assert order.strategy_bucket == "value_rebound"
    assert used.status == RiskApprovalStatus.USED
    assert used.order_id == order.order_id


@pytest.mark.asyncio
async def test_dropped_bucket_fails_closed_and_does_not_consume_approval():
    db = DroppingBucketDatabase()
    db.seed_risk_approval(approval())
    service = ExecutionService(db, NoopBroker())

    with pytest.raises(StrategyBucketPersistenceError) as exc_info:
        await service.create_order(order_request())

    assert exc_info.value.status_code == 409
    assert exc_info.value.diagnostics.requested_bucket == "value_rebound"
    assert exc_info.value.diagnostics.persisted_bucket == "unassigned"

    persisted = await db.get_order_by_trade_id("trade-cinf-value-rebound")
    risk_approval = await db.get_risk_approval("risk-bucket-test")

    assert persisted.status == OrderStatus.FAILED
    assert "strategy_bucket_persistence_mismatch" in persisted.reason
    assert risk_approval.status == RiskApprovalStatus.APPROVED
    assert risk_approval.order_id is None


@pytest.mark.asyncio
async def test_idempotent_order_with_different_bucket_is_rejected():
    db = InMemoryDatabaseClient()
    await db.create_order(order_request(strategy_bucket="unassigned"))
    service = ExecutionService(db, NoopBroker())

    with pytest.raises(StrategyBucketPersistenceError) as exc_info:
        await service.create_order(order_request())

    assert exc_info.value.diagnostics.context == "idempotent_lookup"
    assert exc_info.value.diagnostics.requested_bucket == "value_rebound"
    assert exc_info.value.diagnostics.persisted_bucket == "unassigned"

    existing = await db.get_order_by_trade_id("trade-cinf-value-rebound")
    assert existing.status == OrderStatus.PENDING


def test_report_resolution_never_lets_unassigned_hide_request_bucket():
    request = order_request()
    persisted = request.model_dump()
    persisted.update({"order_id": 77, "status": "pending", "strategy_bucket": "unassigned"})

    from app.models import Order

    order = Order(**persisted)

    assert resolved_strategy_bucket_for_report(request, order) == "value_rebound"


@pytest.mark.asyncio
async def test_http_database_client_sends_strategy_bucket_and_reads_it_back():
    base_url = "http://db-agent"
    client = HttpDatabaseClient(base_url)
    request = order_request(
        symbol="NVTS",
        trade_id="trade-nvts",
        strategy_bucket="news_momentum",
    )

    response_payload = {
        "order_id": 123,
        "trade_id": "trade-nvts",
        "account_id": 1,
        "symbol": "NVTS",
        "side": "buy",
        "order_type": "market",
        "quantity": 5,
        "time_in_force": "GTC",
        "strategy_bucket": "news_momentum",
        "status": "pending",
        "guard_plan": request.guard_plan,
    }

    def responder(http_request: httpx.Request) -> httpx.Response:
        payload = json.loads(http_request.content.decode("utf-8"))
        assert payload["strategy_bucket"] == "news_momentum"
        assert payload["symbol"] == "NVTS"
        return httpx.Response(200, json=response_payload)

    with respx.mock:
        respx.post(f"{base_url}/accounts/1/orders").mock(side_effect=responder)
        order = await client.create_order(request)

    assert order.strategy_bucket == "news_momentum"
