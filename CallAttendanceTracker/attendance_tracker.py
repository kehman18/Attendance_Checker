import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv
from telethon import TelegramClient, functions, types
from pytgcalls import PyTgCalls
from pytgcalls.exceptions import NoActiveGroupCall
from pytgcalls.types import (
    ChatUpdate,
    GroupCallConfig,
    GroupCallParticipant,
    UpdatedGroupCallParticipant,
)


load_dotenv()

api_id = os.getenv("WATCHER_API_ID")
api_hash = os.getenv("WATCHER_API_HASH")
tracked_chat_id = os.getenv("TRACKED_CHAT_ID")

DATABASE_FILE = "attendance.db"
CHECK_INTERVAL_SECONDS = 10
HEARTBEAT_SECONDS = 30

try:
    RECORDING_DELAY_MINUTES = int(
        os.getenv("RECORDING_DELAY_MINUTES", "10")
    )
except ValueError as error:
    raise ValueError(
        "RECORDING_DELAY_MINUTES must be a whole number."
    ) from error

if RECORDING_DELAY_MINUTES < 1:
    raise ValueError("RECORDING_DELAY_MINUTES must be at least 1.")

RECORDING_DELAY_SECONDS = RECORDING_DELAY_MINUTES * 60
RECORDING_TITLE_PREFIX = (
    os.getenv("RECORDING_TITLE_PREFIX", "Attendance Recording").strip()
    or "Attendance Recording"
)

if not api_id or not api_hash or not tracked_chat_id:
    raise ValueError(
        "Your .env file must contain WATCHER_API_ID, WATCHER_API_HASH, and TRACKED_CHAT_ID."
    )

TRACKED_CHAT_ID = int(tracked_chat_id)


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def seconds_between(start_time, end_time):
    start = datetime.fromisoformat(start_time)
    end = datetime.fromisoformat(end_time)
    return max(0, int((end - start).total_seconds()))


def create_database(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS tracked_calls (
            call_id TEXT PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS attendance_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            left_at TEXT,
            seconds_attended INTEGER,
            FOREIGN KEY (call_id) REFERENCES tracked_calls(call_id)
        )
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_attendance_call_user
        ON attendance_sessions(call_id, user_id)
    """)

    connection.commit()


def close_interrupted_sessions(connection):
    rows = connection.execute("""
        SELECT session_id, joined_at, last_seen_at
        FROM attendance_sessions
        WHERE left_at IS NULL
    """).fetchall()

    for session_id, joined_at, last_seen_at in rows:
        connection.execute("""
            UPDATE attendance_sessions
            SET left_at = ?, seconds_attended = ?
            WHERE session_id = ?
        """, (
            last_seen_at,
            seconds_between(joined_at, last_seen_at),
            session_id,
        ))

    connection.commit()


async def main():
    watcher = TelegramClient("watcher", int(api_id), api_hash)
    calls = PyTgCalls(watcher)

    database = sqlite3.connect(DATABASE_FILE)
    create_database(database)
    close_interrupted_sessions(database)

    database_lock = asyncio.Lock()
    call_finished = asyncio.Event()

    active_call_id = None
    watcher_user_id = None
    open_sessions = {}
    name_cache = {}
    recording_task = None

    async def get_display_name(user_id):
        if user_id in name_cache:
            return name_cache[user_id]

        try:
            person = await watcher.get_entity(user_id)

            first_name = getattr(person, "first_name", "") or ""
            last_name = getattr(person, "last_name", "") or ""
            title = getattr(person, "title", "") or ""

            display_name = f"{first_name} {last_name}".strip()

            if not display_name:
                display_name = title

            if not display_name:
                display_name = f"Telegram User {user_id}"

        except Exception:
            display_name = f"Telegram User {user_id}"

        name_cache[user_id] = display_name
        return display_name

    async def get_live_voice_chat():
        """
        Returns the active Telegram group call only when this group already
        has a real, unscheduled live voice chat running.
        """
        try:
            input_peer = await watcher.get_input_entity(TRACKED_CHAT_ID)

            if isinstance(input_peer, types.InputPeerChannel):
                full_chat_result = await watcher(
                    functions.channels.GetFullChannelRequest(
                        channel=types.InputChannel(
                            input_peer.channel_id,
                            input_peer.access_hash,
                        )
                    )
                )

            elif isinstance(input_peer, types.InputPeerChat):
                full_chat_result = await watcher(
                    functions.messages.GetFullChatRequest(
                        chat_id=input_peer.chat_id
                    )
                )

            else:
                return None

            input_call = full_chat_result.full_chat.call

            if not isinstance(input_call, types.InputGroupCall):
                return None

            group_call_result = await watcher(
                functions.phone.GetGroupCallRequest(
                    call=input_call,
                    limit=1,
                )
            )

            group_call = group_call_result.call

            if not isinstance(group_call, types.GroupCall):
                return None

            if group_call.schedule_date is not None:
                return None

            return input_call, group_call

        except Exception:
            return None

    async def has_live_voice_chat():
        return await get_live_voice_chat() is not None

    def make_recording_title():
        timestamp = now_utc().strftime("%Y-%m-%d %H-%M UTC")
        title = f"{RECORDING_TITLE_PREFIX} - {timestamp}"
        return title[:64]

    async def start_recording_after_delay(call_id_to_record):
        """
        Waits until the call has run for the configured period, then starts an
        audio recording only when Telegram shows that no recording is active.
        """
        try:
            await asyncio.sleep(RECORDING_DELAY_SECONDS)

            if active_call_id != call_id_to_record:
                return

            live_voice_chat = await get_live_voice_chat()

            if live_voice_chat is None:
                print(
                    "RECORDING SKIPPED: The voice chat ended before the "
                    "recording check."
                )
                return

            input_call, group_call = live_voice_chat

            if group_call.record_start_date is not None:
                print(
                    "RECORDING ALREADY ACTIVE: Leaving the existing "
                    "Telegram recording unchanged."
                )
                return

            title = make_recording_title()

            await watcher(
                functions.phone.ToggleGroupCallRecordRequest(
                    call=input_call,
                    start=True,
                    title=title,
                    video=False,
                )
            )

            print(f"RECORDING STARTED: {title}")

        except asyncio.CancelledError:
            print("RECORDING TIMER CANCELLED: The voice chat ended.")
            raise

        except Exception as error:
            print(
                "Could not start the recording after "
                f"{RECORDING_DELAY_MINUTES} minutes: "
                f"{type(error).__name__}: {error}"
            )

    async def mark_joined(user_id):
        if active_call_id is None or user_id == watcher_user_id:
            return

        if user_id in open_sessions:
            return

        display_name = await get_display_name(user_id)
        joined_at = now_utc().isoformat()

        async with database_lock:
            if user_id in open_sessions:
                return

            database.execute("""
                INSERT INTO attendance_sessions (
                    call_id,
                    user_id,
                    display_name,
                    joined_at,
                    last_seen_at
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                active_call_id,
                user_id,
                display_name,
                joined_at,
                joined_at,
            ))

            database.commit()
            open_sessions[user_id] = display_name

        print(f"JOINED: {display_name}")

    async def mark_left(user_id):
        if active_call_id is None or user_id not in open_sessions:
            return

        left_at = now_utc().isoformat()
        display_name = open_sessions[user_id]

        async with database_lock:
            row = database.execute("""
                SELECT session_id, joined_at
                FROM attendance_sessions
                WHERE call_id = ?
                  AND user_id = ?
                  AND left_at IS NULL
                ORDER BY session_id DESC
                LIMIT 1
            """, (active_call_id, user_id)).fetchone()

            if row:
                session_id, joined_at = row

                database.execute("""
                    UPDATE attendance_sessions
                    SET left_at = ?, last_seen_at = ?, seconds_attended = ?
                    WHERE session_id = ?
                """, (
                    left_at,
                    left_at,
                    seconds_between(joined_at, left_at),
                    session_id,
                ))

                database.commit()

            open_sessions.pop(user_id, None)

        print(f"LEFT: {display_name}")

    async def heartbeat():
        if active_call_id is None or not open_sessions:
            return

        heartbeat_time = now_utc().isoformat()

        async with database_lock:
            database.execute("""
                UPDATE attendance_sessions
                SET last_seen_at = ?
                WHERE call_id = ?
                  AND left_at IS NULL
            """, (heartbeat_time, active_call_id))

            database.commit()

    async def finish_active_call(reason):
        nonlocal active_call_id, recording_task

        if active_call_id is None:
            return

        if recording_task is not None and not recording_task.done():
            recording_task.cancel()

        recording_task = None

        ended_at = now_utc().isoformat()
        call_id_to_close = active_call_id

        async with database_lock:
            for user_id in list(open_sessions.keys()):
                row = database.execute("""
                    SELECT session_id, joined_at
                    FROM attendance_sessions
                    WHERE call_id = ?
                      AND user_id = ?
                      AND left_at IS NULL
                    ORDER BY session_id DESC
                    LIMIT 1
                """, (call_id_to_close, user_id)).fetchone()

                if row:
                    session_id, joined_at = row

                    database.execute("""
                        UPDATE attendance_sessions
                        SET left_at = ?, last_seen_at = ?, seconds_attended = ?
                        WHERE session_id = ?
                    """, (
                        ended_at,
                        ended_at,
                        seconds_between(joined_at, ended_at),
                        session_id,
                    ))

            database.execute("""
                UPDATE tracked_calls
                SET ended_at = ?
                WHERE call_id = ?
            """, (ended_at, call_id_to_close))

            database.commit()

        print(f"CALL ENDED: {reason}")

        open_sessions.clear()
        active_call_id = None
        call_finished.set()

    async def start_active_call():
        nonlocal active_call_id, recording_task

        active_call_id = uuid4().hex
        started_at = now_utc().isoformat()
        call_finished.clear()

        async with database_lock:
            database.execute("""
                INSERT INTO tracked_calls (call_id, chat_id, started_at)
                VALUES (?, ?, ?)
            """, (
                active_call_id,
                TRACKED_CHAT_ID,
                started_at,
            ))

            database.commit()

        print("\nCALL DETECTED. Attendance tracking has started.")

        recording_task = asyncio.create_task(
            start_recording_after_delay(active_call_id)
        )
        print(
            "A recording check is scheduled for "
            f"{RECORDING_DELAY_MINUTES} minutes from now."
        )

        current_participants = await calls.get_participants(TRACKED_CHAT_ID)

        if current_participants:
            for participant in current_participants:
                await mark_joined(participant.user_id)

    @calls.on_update()
    async def receive_call_update(_, update):
        if update.chat_id != TRACKED_CHAT_ID:
            return

        if isinstance(update, UpdatedGroupCallParticipant):
            participant = update.participant

            if update.action == GroupCallParticipant.Action.JOINED:
                await mark_joined(participant.user_id)

            elif update.action in (
                GroupCallParticipant.Action.LEFT,
                GroupCallParticipant.Action.KICKED,
            ):
                await mark_left(participant.user_id)

        elif isinstance(update, ChatUpdate):
            if update.status & ChatUpdate.Status.LEFT_CALL:
                await finish_active_call(
                    "The voice chat was closed or the watcher left it."
                )

    try:
        print("Starting the attendance tracker...")
        await calls.start()

        watcher_account = await watcher.get_me()
        watcher_user_id = watcher_account.id

        print("Tracker is running.")
        print("It will check for a voice chat every 10 seconds.")
        print(
            "It will check whether recording is active "
            f"{RECORDING_DELAY_MINUTES} minutes into each detected call."
        )
        print("Keep this PowerShell window open during meetings.\n")

        waiting_message_shown = False

        while True:
            if active_call_id is None:
                call_is_live = await has_live_voice_chat()

                if not call_is_live:
                    if not waiting_message_shown:
                        print("Waiting for a group voice chat to begin...")
                        waiting_message_shown = True

                    await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                try:
                    await calls.play(
                        TRACKED_CHAT_ID,
                        config=GroupCallConfig(auto_start=False),
                    )

                    await calls.mute(TRACKED_CHAT_ID)
                    waiting_message_shown = False

                    await start_active_call()

                except NoActiveGroupCall:
                    await asyncio.sleep(CHECK_INTERVAL_SECONDS)

                except Exception as error:
                    print(
                        f"Could not join the active call: "
                        f"{type(error).__name__}: {error}"
                    )
                    await asyncio.sleep(CHECK_INTERVAL_SECONDS)

            else:
                try:
                    await asyncio.wait_for(
                        call_finished.wait(),
                        timeout=HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    await heartbeat()

    finally:
        if active_call_id is not None:
            await finish_active_call("Tracker was stopped.")

        try:
            await calls.leave_call(TRACKED_CHAT_ID)
        except Exception:
            pass

        await watcher.disconnect()
        database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTracker stopped safely.")

