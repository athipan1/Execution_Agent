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
    assert ticket["summary"]["no_action_required_count"] == 1
    assert ticket["summary"]["blocked_count"] == 0
    assert ticket["blocked"] == []
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
                },
                {
                    "symbol": "ACGL",
                    "preview_status": "blocked_missing_reference_price",
                },
            ]
        }
    )

    assert ticket["approval_required"] is True
    assert ticket["summary"]["ready_for_manual_approval_count"] == 1
    assert ticket["summary"]["no_action_required_count"] == 1
    assert ticket["summary"]["blocked_count"] == 1
    assert (
        ticket["next_step"]
        == "review_ticket_then_use_a_separate_approved_execution_workflow"
    )


def test_blocked_only_ticket_requires_resolution_before_refresh():
    ticket = build_order_review_approval_ticket(
        {
            "plans": [
                {
                    "symbol": "ACGL",
                    "preview_status": "blocked_missing_reference_price",
                    "reason": "Reference price is unavailable.",
                }
            ]
        }
    )

    assert ticket["approval_required"] is False
    assert ticket["summary"]["blocked_count"] == 1
    assert ticket["next_step"] == "resolve_blockers_then_refresh_order_review_preview"


def test_requested_missing_symbol_uses_blocked_next_step():
    ticket = build_order_review_approval_ticket(
        {"plans": []},
        {"symbols": ["ACGL"]},
    )

    assert ticket["summary"]["blocked_count"] == 1
    assert ticket["blocked"][0]["preview_status"] == "blocked_symbol_not_found_in_preview"
    assert ticket["next_step"] == "resolve_blockers_then_refresh_order_review_preview"
