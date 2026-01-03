from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    API_KEY: str = "default_api_key"  # Default value for development
    DB_MODE: str = "memory"
    DB_AGENT_URL: Optional[str] = None

    class Config:
        env_file = ".env"

settings = Settings()
