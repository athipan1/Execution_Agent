from app.main import app


def test_trade_plan_execution_route_is_registered():
    paths = {route.path for route in app.routes}

    assert "/execute/trade-plan" in paths
