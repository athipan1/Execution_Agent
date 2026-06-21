from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    API_KEY: str = "dev_execution_key"
    DATABASE_AGENT_API_KEY: Optional[str] = None
    DB_MODE: str = "agent"
    DB_AGENT_URL: Optional[str] = None

    # Trading mode guardrails. Defaults are safe for local/paper workflows.
    TRADING_MODE: str = "PAPER"
    TRADING_ENABLED: bool = False
    ALLOW_LIVE_TRADING: bool = False

    # Broker configuration
    BROKER_MODE: str = "SIMULATOR"  # Can be "SIMULATOR" or "ALPACA"
    BROKER_API_KEY: Optional[str] = None
    BROKER_API_SECRET: Optional[str] = None

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
