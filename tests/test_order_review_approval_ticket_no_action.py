from app.services.order_review_approval_ticket import (
    build_order_review_approval_ticket,
)


def test_no_action_required_is_not_reported_as_blocked():
    ticket = build_order_review_approval_ticket(
        {
            "reward_risk_ratio": 2.0,
            "plans": [
                {
                    "symbol": "CINF",
                    "preview_status": "no_action_required",
                    "reason": "Existing broker protection already matches policy.",
                    "recommended_next_step": "keep_current_orders",
                }
            ],
        }
    )

    assert ticket["approval_required"] is False
    assert ticket["summary"]["ready_for_manual_approval_count"] == 0
    assert ticket["summary"]["no_action_required_count"] == 1
    assert ticket["summary"]["blocked_count"] == 0
    assert ticket["blocked"] == []
    assert ticket["no_action_required"] == [
        {
            "symbol": "CINF",
            "preview_status": "no_action_required",
            "reason": "Existing broker protection already matches policy.",
            "recommended_next_step": "keep_current_orders",
        }
    ]
    assert ticket["next_step"] == "no_manual_approval_required"


def test_ready_no_action_and_blocked_plans_have_separate_counts():
    ticket = build_order_review_approval_ticket(
        {
            "plans": [
                {
                    "symbol": "BKNG",
                    "position_qty": "51",
                    "preview_status": "ready_for_manual_review",
                    "current_stop_order": {"id": "stop-bkng"},
                    "stop_price": 168.0,
                    "take_profit_price": 218.0,
                },
                {
                    "symbol": "CINF",
                    "preview_status": "no_action_required",
                    "reason": "No broker mutation is needed.",
                },
                {
                    "symbol": "ACGL",
                    "preview_status": "blocked_missing_reference_price",
                    "reason": "Reference price is unavailable.",
                },
            ]
        }
    )

    assert ticket["approval_required"] is True
    assert ticket["summary"]["ready_for_manual_approval_count"] == 1
    assert ticket["summary"]["no_action_required_count"] == 1
    assert ticket["summary"]["blocked_count"] == 1
    assert ticket["ready_for_manual_approval"][0]["symbol"] == "BKNG"
    assert ticket["no_action_required"][0]["symbol"] == "CINF"
    assert ticket["blocked"][0]["symbol"] == "ACGL"


def test_requested_no_action_symbol_is_not_replaced_by_not_found_block():
    ticket = build_order_review_approval_ticket(
        {
            "plans": [
                {
                    "symbol": "CINF",
                    "preview_status": "no_action_required",
                    "reason": "Nothing to change.",
                }
            ]
        },
        {"symbols": ["cinf"]},
    )

    assert ticket["requested_symbols"] == ["CINF"]
    assert ticket["summary"]["no_action_required_count"] == 1
    assert ticket["summary"]["blocked_count"] == 0
    assert ticket["blocked"] == []
