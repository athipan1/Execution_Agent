from app.services.protection_diagnostics import build_protection_diagnostics


def test_protection_diagnostics_flags_stop_only_oto_as_needs_bracket_upgrade():
    diagnostics = build_protection_diagnostics(
        positions=[{"symbol": "ACGL", "qty": "82"}],
        open_orders=[
            {
                "id": "stop-1",
                "symbol": "ACGL",
                "side": "sell",
                "qty": "82",
                "type": "stop",
                "order_class": "oto",
                "status": "new",
            }
        ],
    )

    assert diagnostics["mode"] == "diagnostic_only"
    assert diagnostics["safety"] == "read_only_no_orders_submitted"
    assert diagnostics["summary"]["position_count"] == 1
    assert diagnostics["summary"]["stop_only_count"] == 1
    assert diagnostics["summary"]["needs_bracket_upgrade_count"] == 1
    assert diagnostics["summary"]["orders_submitted"] is False

    row = diagnostics["positions"][0]
    assert row["symbol"] == "ACGL"
    assert row["has_protective_stop"] is True
    assert row["has_take_profit"] is False
    assert row["protection_status"] == "stop_only"
    assert row["recommended_action"] == "needs_bracket_upgrade"
    assert row["orders_submitted"] is False


def test_protection_diagnostics_marks_bracket_order_as_protected():
    diagnostics = build_protection_diagnostics(
        positions=[{"symbol": "ADBE", "qty": "52"}],
        open_orders=[
            {
                "id": "bracket-1",
                "symbol": "ADBE",
                "side": "sell",
                "qty": "52",
                "type": "stop",
                "order_class": "bracket",
                "status": "new",
            }
        ],
    )

    assert diagnostics["summary"]["bracket_protected_count"] == 1
    assert diagnostics["summary"]["needs_bracket_upgrade_count"] == 0

    row = diagnostics["positions"][0]
    assert row["has_bracket"] is True
    assert row["has_take_profit"] is True
    assert row["protection_status"] == "bracket_protected"
    assert row["recommended_action"] == "none"


def test_protection_diagnostics_flags_position_without_open_orders_as_unprotected():
    diagnostics = build_protection_diagnostics(
        positions=[{"symbol": "BKNG", "qty": "47"}],
        open_orders=[],
    )

    assert diagnostics["summary"]["unprotected_position_count"] == 1
    row = diagnostics["positions"][0]
    assert row["symbol"] == "BKNG"
    assert row["has_protective_stop"] is False
    assert row["has_take_profit"] is False
    assert row["protection_status"] == "unprotected"
    assert row["recommended_action"] == "needs_protective_order"


def test_protection_diagnostics_groups_orders_by_symbol():
    diagnostics = build_protection_diagnostics(
        positions=[
            {"symbol": "ACGL", "qty": "82"},
            {"symbol": "ADBE", "qty": "52"},
        ],
        open_orders=[
            {"id": "acgl-stop", "symbol": "ACGL", "side": "sell", "qty": "82", "type": "stop", "order_class": "oto"},
            {"id": "adbe-stop", "symbol": "ADBE", "side": "sell", "qty": "52", "type": "stop", "order_class": "bracket"},
        ],
    )

    by_symbol = {row["symbol"]: row for row in diagnostics["positions"]}
    assert by_symbol["ACGL"]["recommended_action"] == "needs_bracket_upgrade"
    assert by_symbol["ADBE"]["recommended_action"] == "none"
    assert diagnostics["summary"]["position_count"] == 2
    assert diagnostics["summary"]["open_order_count"] == 2
    assert diagnostics["summary"]["diagnostic_only"] is True
