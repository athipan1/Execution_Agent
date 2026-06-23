from app.models import CreateOrderRequest
from app.services.bucket_order_safety import validate_bucket_order_batch


def _order(symbol, bucket="core_dividend", trade_id=None):
    return CreateOrderRequest(
        trade_id=trade_id or f"trade-{symbol}",
        account_id=1,
        symbol=symbol,
        side="buy",
        order_type="market",
        quantity=1,
        final_quantity=1,
        risk_approval_id=f"risk-{symbol}",
        strategy_bucket=bucket,
        guard_plan={"stop_loss": 95},
    )


def test_bucket_order_safety_allows_controlled_batch():
    result = validate_bucket_order_batch([
        _order("KO", "core_dividend"),
        _order("JNJ", "core_dividend"),
        _order("ACGL", "value_rebound"),
        _order("ADBE", "value_rebound"),
        _order("NEWS1", "news_momentum"),
    ])

    assert result["approved"] is True
    assert result["errors"] == []
    assert result["summary"]["bucket_counts"]["core_dividend"] == 2
    assert result["summary"]["bucket_counts"]["value_rebound"] == 2
    assert result["summary"]["bucket_counts"]["news_momentum"] == 1


def test_bucket_order_safety_blocks_too_many_orders():
    result = validate_bucket_order_batch([
        _order("A"), _order("B"), _order("C"), _order("D"), _order("E"), _order("F")
    ], max_orders_per_run=5)

    assert result["approved"] is False
    assert any(error["code"] == "MAX_ORDERS_PER_RUN_EXCEEDED" for error in result["errors"])


def test_bucket_order_safety_blocks_duplicate_symbol():
    result = validate_bucket_order_batch([_order("KO", trade_id="a"), _order("KO", trade_id="b")])

    assert result["approved"] is False
    assert any(error["code"] == "DUPLICATE_SYMBOL_IN_BATCH" for error in result["errors"])


def test_bucket_order_safety_blocks_existing_open_symbol():
    result = validate_bucket_order_batch([_order("KO")], existing_open_symbols=["KO"])

    assert result["approved"] is False
    assert any(error["code"] == "SYMBOL_ALREADY_HAS_OPEN_ORDER" for error in result["errors"])


def test_bucket_order_safety_blocks_multiple_news_orders():
    result = validate_bucket_order_batch([
        _order("NEWS1", "news_momentum"),
        _order("NEWS2", "news_momentum"),
    ])

    assert result["approved"] is False
    assert any(error["code"] == "NEWS_MOMENTUM_LIMIT_EXCEEDED" for error in result["errors"])
