from app.manual_order_review_gate_routes import _current_ticket_for_gate
from app.services.order_review_approval_ticket import build_order_review_approval_ticket


def test_manual_review_gate_prefers_full_ticket_when_approving_subset():
    preview = {
        "reward_risk_ratio": 2.0,
        "plans": [
            {
                "symbol": "ACGL",
                "position_qty": "82",
                "preview_status": "ready_for_manual_review",
                "current_stop_order": {
                    "id": "order-acgl",
                    "symbol": "ACGL",
                    "status": "new",
                    "type": "stop",
                },
                "reference_price": 102.20,
                "stop_price": 92.94,
                "take_profit_price": 120.72,
                "reward_risk_ratio": 2.0,
            },
            {
                "symbol": "BKNG",
                "position_qty": "47",
                "preview_status": "ready_for_manual_review",
                "current_stop_order": {
                    "id": "order-bkng",
                    "symbol": "BKNG",
                    "status": "new",
                    "type": "stop",
                },
                "reference_price": 184.56,
                "stop_price": 168.19,
                "take_profit_price": 217.30,
                "reward_risk_ratio": 2.0,
            },
        ],
    }
    full_ticket = build_order_review_approval_ticket(preview, {"reward_risk_ratio": 2.0})

    ticket = _current_ticket_for_gate(
        preview,
        {
            "ticket_id": full_ticket["ticket_id"],
            "confirmation_phrase": "APPROVE_ORDER_REVIEW_TICKET",
            "symbols": ["BKNG"],
            "expected_orders": {
                "BKNG": {
                    "qty": "47",
                    "current_stop_order_id": "order-bkng",
                    "stop_price": 168.19,
                    "take_profit_price": 217.30,
                }
            },
            "reward_risk_ratio": 2.0,
        },
        reward_risk_ratio=2.0,
    )

    assert ticket["ticket_id"] == full_ticket["ticket_id"]
    assert ticket["summary"]["ready_for_manual_approval_count"] == 2
    assert {item["symbol"] for item in ticket["ready_for_manual_approval"]} == {"ACGL", "BKNG"}


def test_manual_review_gate_uses_scoped_ticket_when_submitted_ticket_was_scoped():
    preview = {
        "reward_risk_ratio": 2.0,
        "plans": [
            {
                "symbol": "ACGL",
                "position_qty": "82",
                "preview_status": "ready_for_manual_review",
                "current_stop_order": {"id": "order-acgl", "symbol": "ACGL", "status": "new", "type": "stop"},
                "stop_price": 92.94,
                "take_profit_price": 120.72,
                "reward_risk_ratio": 2.0,
            },
            {
                "symbol": "BKNG",
                "position_qty": "47",
                "preview_status": "ready_for_manual_review",
                "current_stop_order": {"id": "order-bkng", "symbol": "BKNG", "status": "new", "type": "stop"},
                "stop_price": 168.19,
                "take_profit_price": 217.30,
                "reward_risk_ratio": 2.0,
            },
        ],
    }
    scoped_payload = {"symbols": ["BKNG"], "reward_risk_ratio": 2.0}
    scoped_ticket = build_order_review_approval_ticket(preview, scoped_payload)

    ticket = _current_ticket_for_gate(
        preview,
        {**scoped_payload, "ticket_id": scoped_ticket["ticket_id"]},
        reward_risk_ratio=2.0,
    )

    assert ticket["ticket_id"] == scoped_ticket["ticket_id"]
    assert ticket["summary"]["ready_for_manual_approval_count"] == 1
    assert ticket["ready_for_manual_approval"][0]["symbol"] == "BKNG"


def test_order_review_ticket_id_is_stable_when_preview_order_changes():
    preview = {
        "reward_risk_ratio": 2.0,
        "plans": [
            {
                "symbol": "BKNG",
                "position_qty": "47",
                "preview_status": "ready_for_manual_review",
                "current_stop_order": {"id": "order-bkng", "symbol": "BKNG", "status": "new", "type": "stop"},
                "stop_price": 168.19,
                "take_profit_price": 217.30,
                "reward_risk_ratio": 2.0,
            },
            {
                "symbol": "ACGL",
                "position_qty": "82",
                "preview_status": "ready_for_manual_review",
                "current_stop_order": {"id": "order-acgl", "symbol": "ACGL", "status": "new", "type": "stop"},
                "stop_price": 92.94,
                "take_profit_price": 120.72,
                "reward_risk_ratio": 2.0,
            },
        ],
    }
    reordered_preview = {**preview, "plans": list(reversed(preview["plans"]))}

    ticket = build_order_review_approval_ticket(preview, {"reward_risk_ratio": 2.0})
    reordered_ticket = build_order_review_approval_ticket(reordered_preview, {"reward_risk_ratio": 2.0})

    assert reordered_ticket["ticket_id"] == ticket["ticket_id"]
