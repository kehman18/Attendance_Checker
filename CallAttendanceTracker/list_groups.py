import os

from dotenv import load_dotenv
from telethon.sync import TelegramClient

load_dotenv()

api_id = os.getenv("WATCHER_API_ID")
api_hash = os.getenv("WATCHER_API_HASH")

client = TelegramClient("watcher", int(api_id), api_hash)

print("\nGROUPS AVAILABLE TO THE WATCHER ACCOUNT\n")

with client:
    for dialog in client.iter_dialogs():
        if dialog.is_group:
            print(f"Group name: {dialog.name}")
            print(f"Group ID:   {dialog.id}")
            print("-" * 45)