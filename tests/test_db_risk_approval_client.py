from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.db_client import HttpDatabaseClient
from app.models import RiskApprovalStatus


@pytest.mark.asyncio
async def test_http_db_client_get_risk_approval():
    base_url = "http://db-agent"
    client = HttpDatabaseClient(base_url)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    with respx.mock:
        respx.get(f"{base_url}/risk-approvals/risk-123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "approval_id": "risk-123",
                    "account_id": 1,
                    "symbol": "AAPL",
                    "side": "buy",
                    "approved_quantity": 10,
                    "status": "approved",
                    "expires_at": expires_at,
                },
            )
        )
        respx.get(f"{base_url}/risk-approvals/missing").mock(return_value=httpx.Response(404))

        approval = await client.get_risk_approval("risk-123")
        missing = await client.get_risk_approval("missing")

    assert approval.approval_id == "risk-123"
    assert approval.status == RiskApprovalStatus.APPROVED
    assert missing is None


@pytest.mark.asyncio
async def test_http_db_client_mark_risk_approval_used():
    base_url = "http://db-agent"
    client = HttpDatabaseClient(base_url)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    used_at = datetime.now(timezone.utc).isoformat()

    with respx.mock:
        route = respx.post(f"{base_url}/risk-approvals/risk-123/use").mock(
            return_value=httpx.Response(
                200,
                json={
                    "approval_id": "risk-123",
                    "account_id": 1,
                    "symbol": "AAPL",
                    "side": "buy",
                    "approved_quantity": 10,
                    "status": "used",
                    "expires_at": expires_at,
                    "used_at": used_at,
                    "order_id": 99,
                },
            )
        )

        approval = await client.mark_risk_approval_used("risk-123", 99)

    assert route.called
    assert approval.status == RiskApprovalStatus.USED
    assert approval.order_id == 99
