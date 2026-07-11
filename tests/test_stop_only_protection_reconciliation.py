from app.services.protection_reconciliation import (
    build_protection_reconciliation_preview,
)


def test_stop_only_position_is_ready_for_full_oco_reconciliation():
    diagnostics = {
        "positions": [
            {
                "symbol": "ACGL",
                "position_qty": "151",
                "current_price": "101.06",
                "avg_entry_price": "99.96",
                "protection_status": "stop_only",
                "open_orders": [
                    {
                        "id": "stop-1",
                        "symbol": "ACGL",
                        "side": "sell",
                        "qty": "151",
                        "type": "stop",
                        "stop_price": "96.90",
                    }
                ],
            }
        ]
    }
    proposals = [
        {
            "symbol": "ACGL",
            "qty": 151,
            "stop_price": 96.90,
            "take_profit_price": 109.38,
            "risk_policy_version": "risk-existing-position-protection-v1",
            "calculation_method": "preserve_valid_existing_stop",
        }
    ]

    preview = build_protection_reconciliation_preview(diagnostics, proposals)

    assert preview["summary"]["eligible_position_count"] == 1
    assert preview["summary"]["ready_for_manual_review_count"] == 1
    assert preview["summary"]["blocked_count"] == 0
    plan = preview["plans"][0]
    assert plan["current_status"] == "stop_only"
    assert plan["preview_status"] == "ready_for_manual_reconciliation_review"
    assert plan["existing_open_order_ids"] == ["stop-1"]
    assert plan["proposed_actions"][-2]["action"] == "would_submit_full_position_oco"
