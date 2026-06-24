from app.models import OrderSide, OrderType, PortfolioExecutionRequest, PortfolioRiskApproval
from app.services.portfolio_execution import build_order_requests_from_portfolio


def test_build_order_requests_from_portfolio_preserves_risk_metadata():
    request = PortfolioExecutionRequest(
        account_id=1,
        default_price=100.0,
        approvals=[
            PortfolioRiskApproval(
                symbol="AAPL",
                approved=True,
                strategy_bucket="core_dividend",
                risk_approval_id="approval-aapl",
                final_quantity=10,
                risk_response={"entry_price": 101.0},
                guard_plan={"symbol": "AAPL", "side": "sell", "quantity": 10, "trigger_price": 90},
            ),
            PortfolioRiskApproval(
                symbol="MSFT",
                approved=True,
                strategy_bucket="news_momentum",
                risk_response={
                    "approval_id": "approval-msft",
                    "final_quantity": 3,
                    "guard_plan": {"symbol": "MSFT", "side": "sell", "quantity": 3, "trigger_price": 80},
                },
            ),
        ],
        price_by_symbol={"MSFT": 250.0},
    )

    orders, failed = build_order_requests_from_portfolio(request)

    assert failed == []
    assert [order.symbol for order in orders] == ["AAPL", "MSFT"]
    assert orders[0].strategy_bucket == "core_dividend"
    assert orders[0].risk_approval_id == "approval-aapl"
    assert orders[0].quantity == 10
    assert orders[0].final_quantity == 10
    assert orders[0].price == 101.0
    assert orders[1].strategy_bucket == "news_momentum"
    assert orders[1].risk_approval_id == "approval-msft"
    assert orders[1].quantity == 3
    assert orders[1].price == 250.0


def test_build_order_requests_from_portfolio_rejects_non_executable_approvals():
    request = PortfolioExecutionRequest(
        account_id=1,
        default_price=100.0,
        approvals=[
            PortfolioRiskApproval(symbol="AAPL", approved=False, strategy_bucket="core_dividend", final_quantity=10, risk_approval_id="approval-aapl"),
            PortfolioRiskApproval(symbol="MSFT", approved=True, strategy_bucket="news_momentum", final_quantity=0, risk_approval_id="approval-msft"),
            PortfolioRiskApproval(symbol="KO", approved=True, strategy_bucket="core_dividend", final_quantity=5),
            PortfolioRiskApproval(symbol="ACGL", approved=True, strategy_bucket="value_rebound", final_quantity=5, risk_approval_id="approval-acgl"),
        ],
    )

    orders, failed = build_order_requests_from_portfolio(request)

    assert orders == []
    assert [row["symbol"] for row in failed] == ["AAPL", "MSFT", "KO", "ACGL"]
    assert [row["reason"] for row in failed] == [
        "risk_approval_not_approved",
        "missing_positive_final_quantity",
        "missing_risk_approval_id",
        "missing_guard_plan_or_protective_exit",
    ]


def test_build_order_requests_from_portfolio_uses_sell_side_override():
    request = PortfolioExecutionRequest(
        account_id=1,
        default_price=100.0,
        side_by_symbol={"AAPL": OrderSide.SELL},
        order_type=OrderType.MARKET,
        approvals=[
            PortfolioRiskApproval(
                symbol="AAPL",
                approved=True,
                strategy_bucket="core_dividend",
                risk_approval_id="approval-aapl",
                final_quantity=2,
                protective_exit={"type": "manual_exit_required"},
            )
        ],
    )

    orders, failed = build_order_requests_from_portfolio(request)

    assert failed == []
    assert orders[0].side == OrderSide.SELL
    assert orders[0].protective_exit == {"type": "manual_exit_required"}
