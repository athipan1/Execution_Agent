from app.services.protection_reconciliation import build_protection_reconciliation_preview


def test_partial_position_builds_full_oco_preview():
    diagnostics = {
        "positions": [
            {
                "symbol": "ACGL",
                "position_qty": "151",
                "current_price": "102.20",
                "protection_status": "partially_protected",
                "open_orders": [
                    {"id": "tp-54", "symbol": "ACGL", "side": "sell", "qty": "54", "type": "limit"}
                ],
            }
        ]
    }
    proposals = [
        {
            "symbol": "ACGL",
            "qty": 151,
            "stop_price": 95.0,
            "take_profit_price": 116.6,
            "risk_policy_version": "risk-v1",
            "calculation_method": "atr_and_structure",
        }
    ]

    preview = build_protection_reconciliation_preview(diagnostics, proposals)

    assert preview["summary"]["ready_for_manual_review_count"] == 1
    assert preview["summary"]["blocked_count"] == 0
    plan = preview["plans"][0]
    assert plan["preview_status"] == "ready_for_manual_reconciliation_review"
    assert plan["existing_open_order_ids"] == ["tp-54"]
    assert plan["proposed_actions"][0]["action"] == "would_cancel_existing_open_order"
    assert plan["proposed_actions"][1] == {
        "action": "would_submit_full_position_oco",
        "symbol": "ACGL",
        "qty": "151",
        "side": "sell",
        "order_class": "oco",
        "stop_loss": {"stop_price": 95.0},
        "take_profit": {"limit_price": 116.6},
    }
    assert plan["proposed_actions"][2]["action"] == "would_verify_full_position_protection"


def test_unprotected_position_requires_risk_proposal():
    diagnostics = {
        "positions": [
            {
                "symbol": "BKNG",
                "position_qty": "51",
                "current_price": "175.00",
                "protection_status": "unprotected",
                "open_orders": [],
            }
        ]
    }

    preview = build_protection_reconciliation_preview(diagnostics, [])

    assert preview["summary"]["blocked_count"] == 1
    plan = preview["plans"][0]
    assert plan["preview_status"] == "blocked_missing_risk_proposal"
    assert plan["recommended_next_step"] == "request_protection_plan_from_risk_agent"
    assert plan["proposed_actions"] == []


def test_quantity_mismatch_fails_closed():
    diagnostics = {
        "positions": [
            {
                "symbol": "ADBE",
                "position_qty": "102",
                "current_price": "220.00",
                "protection_status": "partially_protected",
                "open_orders": [],
            }
        ]
    }
    proposals = [{"symbol": "ADBE", "qty": 36, "stop_price": 200.0, "take_profit_price": 260.0}]

    preview = build_protection_reconciliation_preview(diagnostics, proposals)

    plan = preview["plans"][0]
    assert plan["preview_status"] == "blocked_risk_quantity_mismatch"
    assert plan["recommended_next_step"] == "regenerate_risk_proposal_for_full_position"


def test_invalid_price_direction_fails_closed():
    diagnostics = {
        "positions": [
            {
                "symbol": "CINF",
                "position_qty": "86",
                "current_price": "120.00",
                "protection_status": "partially_protected",
                "open_orders": [],
            }
        ]
    }
    proposals = [{"symbol": "CINF", "qty": 86, "stop_price": 125.0, "take_profit_price": 130.0}]

    preview = build_protection_reconciliation_preview(diagnostics, proposals)

    plan = preview["plans"][0]
    assert plan["preview_status"] == "blocked_invalid_price_direction"
    assert plan["proposed_actions"] == []


def test_fully_protected_positions_are_not_reconciliation_candidates():
    diagnostics = {
        "positions": [
            {
                "symbol": "MSFT",
                "position_qty": "10",
                "current_price": "500.00",
                "protection_status": "bracket_protected",
            }
        ]
    }

    preview = build_protection_reconciliation_preview(diagnostics, [])

    assert preview["summary"]["eligible_position_count"] == 0
    assert preview["plans"] == []
