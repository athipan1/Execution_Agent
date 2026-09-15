from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db_client import get_db_client
from app.models import RiskApproval, RiskApprovalStatus, OrderSide


@pytest.fixture
def open_clock():
    now = datetime.now(timezone.utc)
    return {"timestamp": now.isoformat(), "is_open": True,
            "next_close": (now + timedelta(hours=1)).isoformat(),
            "next_open": (now + timedelta(days=1)).isoformat()}


@pytest.fixture
def approved_order(monkeypatch):
    """Explicit test-only Risk authority and an isolated simulator/queue."""
    from uuid import uuid4
    from app.main import app, get_execution_service
    from app.db_client import InMemoryDatabaseClient
    from app.adapters.simulator import SimulatorAdapter
    from app.services.execution_service import ExecutionService

    db = InMemoryDatabaseClient()
    service = ExecutionService(db, SimulatorAdapter())
    monkeypatch.setattr(settings, "TRADING_ENABLED", True)
    monkeypatch.setitem(app.dependency_overrides, get_execution_service, lambda: service)

    def prepare(payload):
        payload = dict(payload)
        approval_id = "fixture-" + str(uuid4())
        quantity = payload["quantity"]
        buy = payload["side"] == "buy"
        reference = payload.get("price") or 100
        payload.update(risk_approval_id=approval_id, final_quantity=quantity,
            guard_plan={"symbol":payload["symbol"], "side":"sell" if buy else "buy",
                "quantity":quantity, "trigger_price":reference * (.9 if buy else 1.1),
                "take_profit_price":reference * (1.2 if buy else .8)})
        db.seed_risk_approval(RiskApproval(approval_id=approval_id,
            account_id=payload["account_id"], symbol=payload["symbol"],
            side=OrderSide(payload["side"]), approved_quantity=quantity,
            status=RiskApprovalStatus.APPROVED,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        return payload
    return prepare


@pytest.fixture(autouse=True)
def order_endpoint_test_switch(request, monkeypatch):
    if request.node.path.name == "test_orders.py":
        monkeypatch.setattr(settings, "TRADING" + "_ENABLED", True)
        db = get_db_client()
        if hasattr(db, "seed_risk_approval"):
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
            db.seed_risk_approval(RiskApproval(
                approval_id="risk-test-approval",
                account_id=1,
                symbol="AOT.BK",
                side=OrderSide.BUY,
                approved_quantity=100,
                status=RiskApprovalStatus.APPROVED,
                expires_at=expires_at,
            ))
            db.seed_risk_approval(RiskApproval(
                approval_id="risk-fail-approval",
                account_id=1,
                symbol="FAIL.BK",
                side=OrderSide.BUY,
                approved_quantity=100,
                status=RiskApprovalStatus.APPROVED,
                expires_at=expires_at,
            ))
