from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db_client import get_db_client
from app.models import RiskApproval, RiskApprovalStatus, OrderSide


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
