from app.profit_action_preview import ProfitActionPreviewRequest, build_profit_action_preview


def make_payload(action="hold", quantity=0, approved=True, recommended_stop=None):
    return ProfitActionPreviewRequest(
        position={
            "symbol": "ACGL",
            "side": "long",
            "quantity": 82,
            "entry_price": 96.79,
            "current_price": 98.39,
            "stop_loss": 92.94,
            "strategy_bucket": "value_rebound",
        },
        action={
            "action": action,
            "symbol": "ACGL",
            "quantity": quantity,
            "recommended_stop": recommended_stop,
            "reason": "test action",
            "confidence_score": 0.70,
        },
        risk_result={
            "approved": approved,
            "status": "approved" if approved else "rejected",
            "violations": [] if approved else ["risk_gate_not_approved"],
        },
    )


def test_hold_preview_is_no_op_and_safe():
    result = build_profit_action_preview(make_payload())
    assert result["orders_submitted"] is False
    assert result["approved_for_execution"] is True
    assert result["preview"]["execution_action"] == "no_op"


def test_move_stop_preview():
    result = build_profit_action_preview(make_payload(action="move_stop", recommended_stop=96.79))
    assert result["orders_submitted"] is False
    assert result["preview"]["execution_action"] == "replace_or_create_protective_stop_preview"
    assert result["preview"]["order_preview"]["stop_price"] == 96.79


def test_partial_exit_preview():
    result = build_profit_action_preview(make_payload(action="partial_exit", quantity=20))
    assert result["orders_submitted"] is False
    assert result["preview"]["execution_action"] == "partial_exit_preview"


def test_rejected_risk_gate_blocks_preview_execution():
    result = build_profit_action_preview(make_payload(action="partial_exit", quantity=20, approved=False))
    assert result["approved_for_execution"] is False
    assert "risk_gate_not_approved" in result["blocked_reasons"]


def test_exit_all_requires_manual_approval_by_default():
    result = build_profit_action_preview(make_payload(action="exit_all", quantity=82))
    assert result["orders_submitted"] is False
    assert result["approved_for_execution"] is False
    assert "exit_all_manual_approval_required" in result["blocked_reasons"]
