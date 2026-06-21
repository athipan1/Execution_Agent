from app.adapters.alpaca import AlpacaAdapter
from app.adapters.base import BrokerAdapter
from app.adapters.simulator import SimulatorAdapter
from app.config import settings
from app.db_client import get_db_client
from app.services.execution_service import ExecutionService


def trading_mode() -> str:
    return str(settings.TRADING_MODE or "PAPER").upper()


def broker_mode() -> str:
    return str(settings.BROKER_MODE or "SIMULATOR").upper()


def validate_worker_configuration() -> str:
    mode = trading_mode()
    broker = broker_mode()
    if mode not in {"PAPER", "LIVE"}:
        raise RuntimeError("TRADING_MODE must be PAPER or LIVE.")
    if mode == "LIVE":
        if not settings.ALLOW_LIVE_TRADING:
            raise RuntimeError("LIVE execution worker requires ALLOW_LIVE_TRADING=true.")
        if broker != "ALPACA":
            raise RuntimeError("LIVE execution worker requires BROKER_MODE=ALPACA.")
    if broker not in {"SIMULATOR", "ALPACA"}:
        raise RuntimeError(f"Unsupported BROKER_MODE '{settings.BROKER_MODE}'.")
    return broker


def build_broker_adapter() -> BrokerAdapter:
    broker = validate_worker_configuration()
    if broker == "ALPACA":
        return AlpacaAdapter()
    return SimulatorAdapter()


def build_execution_service() -> ExecutionService:
    return ExecutionService(get_db_client(), build_broker_adapter())
