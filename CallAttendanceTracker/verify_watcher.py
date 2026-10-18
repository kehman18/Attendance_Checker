import os

from dotenv import load_dotenv
from telethon.sync import TelegramClient

load_dotenv()

api_id = os.getenv("WATCHER_API_ID")
api_hash = os.getenv("WATCHER_API_HASH")
phone = os.getenv("WATCHER_PHONE")

if not api_id or not api_hash or not phone:
    raise ValueError(
        "Your .env file is incomplete. Check WATCHER_API_ID, "
        "WATCHER_API_HASH, and WATCHER_PHONE."
    )

client = TelegramClient("watcher", int(api_id), api_hash)

try:
    client.start(phone=phone)

    me = client.get_me()

    print("\nSUCCESS: Watcher account connected to Telegram.")
    print(f"Name: {me.first_name}")
    print(f"Username: @{me.username}" if me.username else "Username: None")
    print(f"Telegram ID: {me.id}")

finally:
    client.disconnect()