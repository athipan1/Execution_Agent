from app.main import app


def test_trade_plan_execution_route_is_registered():
    paths = set(app.openapi()["paths"])

    assert "/execute/trade-plan" in paths
