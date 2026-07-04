from app.services.manual_order_review_gate import build_manual_order_review_gate


def test_manual_review_gate_validates_paper_request():
    result = build_manual_order_review_gate(
        payload={
            "ticket_id": "ticket-1",
            "confirmation_phrase": "APPROVE_ORDER_REVIEW_TICKET",
            "symbols": ["BKNG"],
            "expected_orders": {
                "BKNG": {
                    "qty": "47",
                    "current_stop_order_id": "order-1",
                    "stop_price": 168.19,
                    "take_profit_price": 217.30,
                }
            },
        },
        ticket={
            "ticket_id": "ticket-1",
            "ready_for_manual_approval": [
                {
                    "symbol": "BKNG",
                    "position_qty": "47",
                    "current_stop_order_id": "order-1",
                    "current_stop_order": {"symbol": "BKNG", "status": "new"},
                    "stop_price": 168.19,
                    "take_profit_price": 217.30,
                }
            ],
        },
        account={"paper": True, "status": "ACTIVE", "account_blocked": False, "trading_blocked": False},
        broker_mode="ALPACA",
        trading_mode="PAPER",
    )

    assert result["status"] == "validated"
    assert result["approval_valid"] is True
    assert result["execution_enabled"] is False
    assert result["summary"]["orders_changed"] is False


def test_manual_review_gate_blocks_live_request():
    result = build_manual_order_review_gate(
        payload={"ticket_id": "ticket-1", "confirmation_phrase": "APPROVE_ORDER_REVIEW_TICKET", "symbols": ["BKNG"]},
        ticket={"ticket_id": "ticket-1", "ready_for_manual_approval": []},
        account={"paper": False, "status": "ACTIVE"},
        broker_mode="ALPACA",
        trading_mode="LIVE",
    )

    assert result["status"] == "blocked"
    assert result["approval_valid"] is False
