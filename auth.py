import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

# Telegram API configuration
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME")

if not all([API_ID, API_HASH, SESSION_NAME]):
    raise ValueError("API_ID, API_HASH, and SESSION_NAME must be set in .env file or environment variables.")

API_ID = int(API_ID)

async def main():
    print(f"Initializing session '{SESSION_NAME}'...")
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    print("Client Created and Authorized!")
    print(f"Session file '{SESSION_NAME}.session' should now be created.")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
