from app.services.order_review_plan import build_order_review_plan


def test_order_review_plan_blocks_stop_only_when_stop_price_is_missing():
    diagnostics = {
        "positions": [
            {
                "symbol": "ACGL",
                "position_qty": "82",
                "current_price": "102.20",
                "protection_status": "stop_only",
                "recommended_action": "needs_bracket_upgrade",
                "protective_orders": [
                    {
                        "id": "stop-1",
                        "symbol": "ACGL",
                        "side": "sell",
                        "qty": "82",
                        "type": "stop",
                        "order_class": "oto",
                    }
                ],
            }
        ]
    }

    preview = build_order_review_plan(diagnostics)

    assert preview["mode"] == "preview_only"
    assert preview["safety"] == "read_only_no_orders_submitted_no_orders_cancelled"
    assert preview["summary"]["candidate_count"] == 1
    assert preview["summary"]["blocked_count"] == 1
    assert preview["summary"]["orders_submitted"] is False
    assert preview["summary"]["orders_cancelled"] is False

    plan = preview["plans"][0]
    assert plan["symbol"] == "ACGL"
    assert plan["preview_status"] == "blocked_missing_stop_price"
    assert plan["recommended_next_step"] == "fetch_full_broker_order_details_before_cancel_replace"
    assert plan["proposed_actions"] == []
    assert plan["orders_submitted"] is False
    assert plan["orders_cancelled"] is False


def test_order_review_plan_builds_manual_review_actions_when_prices_are_available():
    diagnostics = {
        "positions": [
            {
                "symbol": "ADBE",
                "position_qty": "52",
                "current_price": "220.00",
                "protection_status": "stop_only",
                "recommended_action": "needs_bracket_upgrade",
                "protective_orders": [
                    {
                        "id": "stop-2",
                        "symbol": "ADBE",
                        "side": "sell",
                        "qty": "52",
                        "type": "stop",
                        "order_class": "oto",
                        "stop_price": "200.00",
                    }
                ],
            }
        ]
    }

    preview = build_order_review_plan(diagnostics, reward_risk_ratio=2.0)

    assert preview["summary"]["candidate_count"] == 1
    assert preview["summary"]["ready_for_manual_review_count"] == 1
    assert preview["summary"]["blocked_count"] == 0

    plan = preview["plans"][0]
    assert plan["preview_status"] == "ready_for_manual_review"
    assert plan["reference_price"] == 220.0
    assert plan["stop_price"] == 200.0
    assert plan["take_profit_price"] == 260.0
    assert plan["orders_submitted"] is False
    assert plan["orders_cancelled"] is False
    assert plan["proposed_actions"] == [
        {
            "action": "would_cancel_existing_stop_order",
            "broker_order_id": "stop-2",
            "symbol": "ADBE",
        },
        {
            "action": "would_submit_bracket_replacement",
            "symbol": "ADBE",
            "qty": "52",
            "side": "sell",
            "stop_loss": {"stop_price": 200.0},
            "take_profit": {"limit_price": 260.0},
            "order_class": "bracket",
        },
    ]


def test_order_review_plan_marks_bracket_protected_as_no_action():
    diagnostics = {
        "positions": [
            {
                "symbol": "MSFT",
                "position_qty": "10",
                "protection_status": "bracket_protected",
                "recommended_action": "none",
            }
        ]
    }

    preview = build_order_review_plan(diagnostics)

    assert preview["summary"]["candidate_count"] == 0
    assert preview["summary"]["no_action_count"] == 1
    assert preview["plans"][0]["preview_status"] == "no_action_required"
    assert preview["plans"][0]["proposed_actions"] == []


def test_order_review_plan_blocks_unprotected_positions():
    diagnostics = {
        "positions": [
            {
                "symbol": "BKNG",
                "position_qty": "47",
                "protection_status": "unprotected",
                "recommended_action": "needs_protective_order",
            }
        ]
    }

    preview = build_order_review_plan(diagnostics)

    assert preview["summary"]["blocked_count"] == 1
    plan = preview["plans"][0]
    assert plan["preview_status"] == "blocked_unprotected_position"
    assert plan["recommended_next_step"] == "create_protective_order_from_risk_agent_before_any_upgrade_flow"
    assert plan["proposed_actions"] == []
