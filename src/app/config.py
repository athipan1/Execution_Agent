from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    API_KEY: str = "dev_execution_key"
    DATABASE_AGENT_API_KEY: Optional[str] = None
    DB_MODE: str = "agent"
    DB_AGENT_URL: Optional[str] = None
    BROKER_SYNC_ENDPOINT: str = "/broker-sync"
    BROKER_SYNC_TIMEOUT_SECONDS: float = 20.0
    FAIL_RECONCILE_WHEN_DB_SYNC_FAILS: bool = False

    # Trading mode guardrails. Defaults are safe for local/paper workflows.
    TRADING_MODE: str = "PAPER"
    TRADING_ENABLED: bool = False
    ALLOW_LIVE_TRADING: bool = False

    # Broker configuration
    BROKER_MODE: str = "SIMULATOR"  # Can be "SIMULATOR" or "ALPACA"
    BROKER_API_KEY: Optional[str] = None
    BROKER_API_SECRET: Optional[str] = None

    # Broker pre-execution safety guards.
    REQUIRE_BROKER_PREFLIGHT: bool = True
    BLOCK_BUY_WHEN_NO_BUYING_POWER: bool = True
    MIN_BUYING_POWER_AFTER_ORDER: float = 0.0
    MAX_STALE_OPEN_ORDER_AGE_MINUTES: int = 390
    FAIL_ON_STALE_OPEN_ORDERS: bool = True
    FAIL_ON_ACCOUNT_RESTRICTED: bool = True

    # Worker configuration
    EXECUTION_WORKER_POLL_SECONDS: float = 2.0
    RECONCILIATION_WORKER_POLL_SECONDS: float = 30.0
    RECONCILIATION_LIMIT: int = 100
    WORKER_RUN_ONCE: bool = False

    # Alpaca configuration
    ALPACA_API_KEY_ID: Optional[str] = None
    ALPACA_SECRET_KEY: Optional[str] = None
    ALPACA_API_URL: str = "https://paper-api.alpaca.markets"

    def assert_live_safe(self) -> None:
        """Fail fast when a LIVE deployment still uses local/dev defaults."""
        if str(self.TRADING_MODE or "PAPER").upper() != "LIVE":
            return
        if not self.API_KEY or self.API_KEY == "dev_execution_key" or self.API_KEY.startswith("dev_"):
            raise RuntimeError("Default/dev API_KEY is forbidden in LIVE mode.")
        if not self.DB_AGENT_URL:
            raise RuntimeError("DB_AGENT_URL is required in LIVE mode.")

    class Config:
        env_file = ".env"


settings = Settings()
settings.assert_live_safe()
