from app.models import Order, OrderSide, OrderStatus, OrderType, TimeInForce
from app.services.protective_order_service import alpaca_price, build_alpaca_entry_payload


def _order(**kwargs):
    values = {
        "order_id": 2,
        "trade_id": "portfolio-ACGL-1",
        "account_id": 1,
        "symbol": "ACGL",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": 82,
        "price": 100,
        "time_in_force": TimeInForce.GTC,
        "status": OrderStatus.PENDING,
        "guard_plan": {
            "symbol": "ACGL",
            "side": "sell",
            "quantity": 82,
            "trigger_price": 92.984,
        },
    }
    values.update(kwargs)
    return Order(**values)


def test_alpaca_price_rounds_us_equity_prices_to_two_decimals_above_one_dollar():
    assert alpaca_price(92.984, "stop_loss.stop_price") == "92.98"
    assert alpaca_price("92.985", "stop_loss.stop_price") == "92.99"
    assert alpaca_price("1.005", "limit_price") == "1.01"


def test_alpaca_price_allows_four_decimals_below_one_dollar():
    assert alpaca_price("0.12345", "stop_loss.stop_price") == "0.1235"
    assert alpaca_price("0.99994", "stop_loss.stop_price") == "0.9999"


def test_build_alpaca_entry_payload_normalizes_stop_loss_price_precision():
    payload = build_alpaca_entry_payload(_order())

    assert payload["order_class"] == "oto"
    assert payload["stop_loss"] == {"stop_price": "92.98"}


def test_build_alpaca_entry_payload_normalizes_limit_and_take_profit_prices():
    order = _order(
        order_type=OrderType.LIMIT,
        price=100.009,
        guard_plan={
            "symbol": "ACGL",
            "side": "sell",
            "quantity": 82,
            "trigger_price": 92.984,
            "take_profit_price": 108.995,
        },
    )

    payload = build_alpaca_entry_payload(order)

    assert payload["limit_price"] == "100.01"
    assert payload["order_class"] == "bracket"
    assert payload["stop_loss"] == {"stop_price": "92.98"}
    assert payload["take_profit"] == {"limit_price": "109"}
