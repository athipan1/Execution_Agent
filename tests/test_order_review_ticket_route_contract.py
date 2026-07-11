import pytest

import app.trade_plan_execution as routes


class _FakeAdapter:
    async def get_positions(self):
        return []

    async def get_open_orders(self):
        return []


@pytest.mark.asyncio
async def test_manual_approval_ticket_route_exposes_aggregate_status(monkeypatch):
    monkeypatch.setattr(
        routes,
        "build_protection_diagnostics",
        lambda positions, open_orders: {"summary": {}, "positions": []},
    )
    monkeypatch.setattr(
        routes,
        "build_order_review_plan",
        lambda diagnostics, reward_risk_ratio: {
            "reward_risk_ratio": reward_risk_ratio,
            "plans": [
                {
                    "symbol": "CINF",
                    "preview_status": "no_action_required",
                    "reason": "Existing protection already matches policy.",
                }
            ],
        },
    )

    response = await routes.broker_order_review_manual_approval_ticket(
        payload={"reward_risk_ratio": 2.0},
        adapter=_FakeAdapter(),
    )
    ticket = response.data

    assert ticket["ticket_status"] == "no_action_required"
    assert ticket["requires_operator_attention"] is False
    assert ticket["summary"]["requires_operator_attention"] is False
    assert ticket["summary"]["no_action_required_count"] == 1
    assert ticket["next_step"] == "no_manual_approval_required"
    assert response.confidence_score == 1.0


@pytest.mark.asyncio
async def test_manual_approval_ticket_route_marks_blockers_for_attention(monkeypatch):
    monkeypatch.setattr(
        routes,
        "build_protection_diagnostics",
        lambda positions, open_orders: {"summary": {}, "positions": []},
    )
    monkeypatch.setattr(
        routes,
        "build_order_review_plan",
        lambda diagnostics, reward_risk_ratio: {
            "reward_risk_ratio": reward_risk_ratio,
            "plans": [
                {
                    "symbol": "ACGL",
                    "preview_status": "blocked_missing_reference_price",
                    "reason": "Reference price is unavailable.",
                }
            ],
        },
    )

    response = await routes.broker_order_review_manual_approval_ticket(
        payload={"reward_risk_ratio": 2.0},
        adapter=_FakeAdapter(),
    )
    ticket = response.data

    assert ticket["ticket_status"] == "blocked"
    assert ticket["requires_operator_attention"] is True
    assert ticket["summary"]["requires_operator_attention"] is True
    assert ticket["summary"]["blocked_count"] == 1
    assert ticket["next_step"] == "resolve_blockers_then_refresh_order_review_preview"
    assert response.confidence_score == 0.7
