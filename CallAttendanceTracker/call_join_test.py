import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from pytgcalls import PyTgCalls
from pytgcalls.exceptions import NoActiveGroupCall
from pytgcalls.types import GroupCallConfig


load_dotenv()

api_id = os.getenv("WATCHER_API_ID")
api_hash = os.getenv("WATCHER_API_HASH")
tracked_chat_id = os.getenv("TRACKED_CHAT_ID")

if not api_id or not api_hash or not tracked_chat_id:
    raise ValueError(
        "Your .env file must contain WATCHER_API_ID, WATCHER_API_HASH, and TRACKED_CHAT_ID."
    )


async def main():
    # These MUST be created inside main(), after asyncio has started its loop.
    watcher = TelegramClient("watcher", int(api_id), api_hash)
    calls = PyTgCalls(watcher)

    joined_call = False

    try:
        print("Connecting the watcher account...")
        await calls.start()

        print("Looking for an active group call...")
        await calls.play(
            int(tracked_chat_id),
            config=GroupCallConfig(auto_start=False),
        )

        await calls.mute(int(tracked_chat_id))
        joined_call = True

        print("\nSUCCESS: The watcher joined the group call muted.")
        print("It will stay for 20 seconds, then leave automatically.")
        await asyncio.sleep(20)

    except NoActiveGroupCall:
        print("\nNO ACTIVE CALL FOUND.")
        print("Start a group voice chat first, then run this file again.")

    except Exception as error:
        print("\nCALL TEST FAILED.")
        print(f"{type(error).__name__}: {error}")

    finally:
        if joined_call:
            try:
                await calls.leave_call(int(tracked_chat_id))
                print("Watcher left the group call.")
            except Exception as error:
                print(f"Could not leave cleanly: {error}")

        await watcher.disconnect()


if __name__ == "__main__":
    asyncio.run(main())