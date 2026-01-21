import asyncio
import sys

from app.adapters.alpaca import AlpacaAdapter
from app.config import settings

async def main():
    """
    Main function to run the Alpaca connection check.
    """
    print("--- Running Alpaca Connection Check ---")

    # Verify that necessary environment variables are set
    if not settings.ALPACA_API_KEY_ID or not settings.ALPACA_SECRET_KEY:
        print("\n[ERROR] Missing required environment variables.")
        print("Please set ALPACA_API_KEY_ID and ALPACA_SECRET_KEY in your .env file or environment.")
        sys.exit(1)

    print(f"Alpaca API Key ID: {settings.ALPACA_API_KEY_ID[:4]}... (loaded)")
    print("Checking connection to Alpaca API...")

    adapter = AlpacaAdapter()
    is_connected = await adapter.check_connection()

    if is_connected:
        print("\n[SUCCESS] Connection to Alpaca was successful!")
    else:
        print("\n[FAILURE] Could not connect to Alpaca.")
        print("Please check your credentials, network connection, and Alpaca API status.")

    print("\n--- Check Complete ---")

if __name__ == "__main__":
    asyncio.run(main())
