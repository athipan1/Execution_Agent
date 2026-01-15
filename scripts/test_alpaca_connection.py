# scripts/test_alpaca_connection.py

import asyncio
import os
import sys

# Add the project root to the Python path to allow importing from 'app'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.adapters.alpaca import AlpacaAdapter
from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)

async def main():
    """
    Tests the connection to Alpaca by attempting to authenticate.
    """
    logger.info("Starting Alpaca connection test.")

    # Temporarily override settings with environment variables from the CI runner
    client_id = os.getenv("ALPACA_CLIENT_ID")
    client_secret = os.getenv("ALPACA_CLIENT_SECRET")

    if not client_id or not client_secret:
        logger.error("ALPACA_CLIENT_ID and ALPACA_CLIENT_SECRET must be set as environment variables.")
        sys.exit(1)

    settings.ALPACA_CLIENT_ID = client_id
    settings.ALPACA_CLIENT_SECRET = client_secret

    adapter = AlpacaAdapter()
    access_token = await adapter._get_access_token()

    if access_token:
        logger.info("Successfully connected to Alpaca and obtained an access token.")
        sys.exit(0)
    else:
        logger.error("Failed to connect to Alpaca and obtain an access token.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
