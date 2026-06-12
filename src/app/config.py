from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    API_KEY: str = "dev_execution_key"
    DATABASE_AGENT_API_KEY: Optional[str] = None
    DB_MODE: str = "agent"
    DB_AGENT_URL: Optional[str] = None

    # Broker configuration
    BROKER_MODE: str = "SIMULATOR"  # Can be "SIMULATOR" or "ALPACA"
    BROKER_API_KEY: Optional[str] = None
    BROKER_API_SECRET: Optional[str] = None

    # Alpaca configuration
    ALPACA_API_KEY_ID: Optional[str] = None
    ALPACA_SECRET_KEY: Optional[str] = None
    ALPACA_API_URL: str = "https://paper-api.alpaca.markets"

    class Config:
        env_file = ".env"

settings = Settings()
