from unittest.mock import patch

import pytest

from app.adapters.simulator import SimulatorAdapter
from app.main import get_broker_adapter as get_main_broker_adapter
from app.trade_plan_execution import get_broker_adapter as get_trade_plan_broker_adapter
from app.workers.bootstrap import build_broker_adapter


def test_test_mode_uses_simulator_even_when_alpaca_is_configured():
    with (
        patch("app.main.settings.TEST_MODE", True),
        patch("app.main.settings.TRADING_MODE", "PAPER"),
        patch("app.main.settings.ALLOW_LIVE_TRADING", False),
        patch("app.main.settings.BROKER_MODE", "ALPACA"),
        patch("app.main.AlpacaAdapter", side_effect=AssertionError("Alpaca must not be instantiated")),
    ):
        adapter = get_main_broker_adapter()

    assert isinstance(adapter, SimulatorAdapter)


def test_trade_plan_test_mode_uses_simulator_even_when_alpaca_is_configured():
    with (
        patch("app.trade_plan_execution.settings.TEST_MODE", True),
        patch("app.trade_plan_execution.settings.TRADING_MODE", "PAPER"),
        patch("app.trade_plan_execution.settings.ALLOW_LIVE_TRADING", False),
        patch("app.trade_plan_execution.settings.BROKER_MODE", "ALPACA"),
        patch(
            "app.trade_plan_execution.HydratedAlpacaAdapter",
            side_effect=AssertionError("Alpaca must not be instantiated"),
        ),
    ):
        adapter = get_trade_plan_broker_adapter()

    assert isinstance(adapter, SimulatorAdapter)


def test_worker_test_mode_uses_simulator_even_when_alpaca_is_configured():
    with (
        patch("app.workers.bootstrap.settings.TEST_MODE", True),
        patch("app.workers.bootstrap.settings.TRADING_MODE", "PAPER"),
        patch("app.workers.bootstrap.settings.ALLOW_LIVE_TRADING", False),
        patch("app.workers.bootstrap.settings.BROKER_MODE", "ALPACA"),
        patch(
            "app.workers.bootstrap.AlpacaAdapter",
            side_effect=AssertionError("Alpaca must not be instantiated"),
        ),
    ):
        adapter = build_broker_adapter()

    assert isinstance(adapter, SimulatorAdapter)


@pytest.mark.parametrize(
    "module_path",
    [
        "app.main.settings",
        "app.trade_plan_execution.settings",
        "app.workers.bootstrap.settings",
    ],
)
def test_test_mode_rejects_live_or_live_authorization(module_path):
    with (
        patch(f"{module_path}.TEST_MODE", True),
        patch(f"{module_path}.TRADING_MODE", "LIVE"),
        patch(f"{module_path}.ALLOW_LIVE_TRADING", True),
        patch(f"{module_path}.BROKER_MODE", "ALPACA"),
    ):
        getter = (
            get_main_broker_adapter
            if module_path.startswith("app.main")
            else get_trade_plan_broker_adapter
            if module_path.startswith("app.trade_plan_execution")
            else build_broker_adapter
        )
        with pytest.raises(RuntimeError, match="TEST_MODE requires"):
            getter()
