from app.services.protection_diagnostics import build_protection_diagnostics


def test_protection_diagnostics_flags_stop_only_oto_as_needs_bracket_upgrade():
    diagnostics = build_protection_diagnostics(
        positions=[{"symbol": "ACGL", "qty": "82", "current_price": "102.20", "avg_entry_price": "96.79"}],
        open_orders=[
            {
                "id": "stop-1",
                "symbol": "ACGL",
                "side": "sell",
                "qty": "82",
                "type": "stop",
                "order_class": "oto",
                "status": "new",
                "stop_price": "95.00",
            }
        ],
    )

    assert diagnostics["mode"] == "diagnostic_only"
    assert diagnostics["summary"]["stop_only_count"] == 1
    assert diagnostics["summary"]["needs_bracket_upgrade_count"] == 1

    row = diagnostics["positions"][0]
    assert row["has_protective_stop"] is True
    assert row["has_take_profit"] is False
    assert row["protection_status"] == "stop_only"
    assert row["recommended_action"] == "needs_bracket_upgrade"
    assert row["stop_covered_qty"] == 82.0


def test_nested_bracket_legs_are_fully_protected():
    diagnostics = build_protection_diagnostics(
        positions=[{"symbol": "ADBE", "qty": "52"}],
        open_orders=[
            {
                "id": "parent-1",
                "symbol": "ADBE",
                "side": "sell",
                "qty": "52",
                "type": "limit",
                "order_class": "bracket",
                "status": "new",
                "limit_price": "250.00",
                "legs": [
                    {
                        "id": "stop-leg",
                        "symbol": "ADBE",
                        "side": "sell",
                        "qty": "52",
                        "type": "stop",
                        "status": "new",
                        "stop_price": "200.00",
                    },
                    {
                        "id": "tp-leg",
                        "symbol": "ADBE",
                        "side": "sell",
                        "qty": "52",
                        "type": "limit",
                        "status": "new",
                        "limit_price": "250.00",
                    },
                ],
            }
        ],
    )

    row = diagnostics["positions"][0]
    assert diagnostics["summary"]["bracket_protected_count"] == 1
    assert diagnostics["summary"]["flattened_order_count"] == 3
    assert row["has_bracket"] is True
    assert row["protection_status"] == "bracket_protected"
    assert row["unprotected_stop_qty"] == 0.0
    assert row["unprotected_take_profit_qty"] == 0.0


def test_bracket_parent_without_concrete_stop_leg_is_not_marked_protected():
    diagnostics = build_protection_diagnostics(
        positions=[{"symbol": "ACGL", "qty": "151"}],
        open_orders=[
            {
                "id": "bracket-parent",
                "symbol": "ACGL",
                "side": "sell",
                "qty": "54",
                "type": "limit",
                "order_class": "bracket",
                "status": "new",
                "stop_price": None,
                "limit_price": "112.84",
                "legs": None,
            }
        ],
    )

    row = diagnostics["positions"][0]
    assert row["has_protective_stop"] is False
    assert row["has_take_profit"] is True
    assert row["has_bracket"] is False
    assert row["protection_status"] == "partially_protected"
    assert row["unprotected_stop_qty"] == 151.0
    assert row["unprotected_take_profit_qty"] == 97.0


def test_partial_stop_quantity_is_not_fully_protected():
    diagnostics = build_protection_diagnostics(
        positions=[{"symbol": "ACGL", "qty": "151"}],
        open_orders=[
            {
                "id": "partial-stop",
                "symbol": "ACGL",
                "side": "sell",
                "qty": "54",
                "type": "stop",
                "status": "new",
                "stop_price": "92.00",
            }
        ],
    )

    row = diagnostics["positions"][0]
    assert diagnostics["summary"]["partially_protected_position_count"] == 1
    assert row["protection_status"] == "partially_protected"
    assert row["recommended_action"] == "reconcile_protective_order_quantities"
    assert row["stop_covered_qty"] == 54.0
    assert row["unprotected_stop_qty"] == 97.0


def test_protection_diagnostics_flags_position_without_open_orders_as_unprotected():
    diagnostics = build_protection_diagnostics(
        positions=[{"symbol": "BKNG", "qty": "47"}],
        open_orders=[],
    )

    assert diagnostics["summary"]["unprotected_position_count"] == 1
    row = diagnostics["positions"][0]
    assert row["symbol"] == "BKNG"
    assert row["protection_status"] == "unprotected"
    assert row["recommended_action"] == "needs_protective_order"


def test_cancelled_orders_do_not_count_as_protection():
    diagnostics = build_protection_diagnostics(
        positions=[{"symbol": "BKNG", "qty": "47"}],
        open_orders=[
            {
                "id": "cancelled-stop",
                "symbol": "BKNG",
                "side": "sell",
                "qty": "47",
                "type": "stop",
                "status": "canceled",
                "stop_price": "160.00",
            }
        ],
    )

    row = diagnostics["positions"][0]
    assert row["has_protective_stop"] is False
    assert row["protection_status"] == "unprotected"
