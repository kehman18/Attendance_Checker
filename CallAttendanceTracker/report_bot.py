import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


load_dotenv()

BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN")
tracked_chat_id = os.getenv("TRACKED_CHAT_ID")

BASE_FOLDER = os.path.dirname(os.path.abspath(__file__))
DATABASE_FILE = os.path.join(BASE_FOLDER, "attendance.db")
LAGOS_TIMEZONE = ZoneInfo("Africa/Lagos")

if not BOT_TOKEN or not tracked_chat_id:
    raise ValueError(
        "Your .env file must contain REPORT_BOT_TOKEN and TRACKED_CHAT_ID."
    )

TRACKED_CHAT_ID = int(tracked_chat_id)


def format_duration(total_seconds):
    total_minutes = total_seconds // 60
    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


def open_database():
    database = sqlite3.connect(DATABASE_FILE, timeout=15)
    database.execute("PRAGMA busy_timeout = 15000")
    return database


def attendance_totals(start_time, end_time):
    """
    Returns total attendance for each person whose session overlaps
    with the requested period.
    """
    if not os.path.exists(DATABASE_FILE):
        return {}

    database = open_database()

    try:
        rows = database.execute("""
            SELECT
                user_id,
                display_name,
                joined_at,
                COALESCE(left_at, last_seen_at) AS effective_left_at
            FROM attendance_sessions
            WHERE joined_at < ?
              AND COALESCE(left_at, last_seen_at) > ?
        """, (
            end_time.isoformat(),
            start_time.isoformat(),
        )).fetchall()

    finally:
        database.close()

    totals = defaultdict(lambda: {"name": "", "seconds": 0})

    for user_id, display_name, joined_at, left_at in rows:
        joined_time = datetime.fromisoformat(joined_at)
        left_time = datetime.fromisoformat(left_at)

        actual_start = max(joined_time, start_time)
        actual_end = min(left_time, end_time)

        seconds = max(0, int((actual_end - actual_start).total_seconds()))

        if seconds > 0:
            totals[user_id]["name"] = display_name
            totals[user_id]["seconds"] += seconds

    return dict(totals)


def today_range():
    now = datetime.now(LAGOS_TIMEZONE)

    start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    end = start + timedelta(days=1)

    return (
        start.astimezone(timezone.utc),
        end.astimezone(timezone.utc),
    )


def week_range():
    now = datetime.now(LAGOS_TIMEZONE)

    start_of_today = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    start_of_week = start_of_today - timedelta(days=now.weekday())

    return (
        start_of_week.astimezone(timezone.utc),
        now.astimezone(timezone.utc),
    )


def is_tracked_group(update):
    return (
        update.effective_chat is not None
        and update.effective_chat.id == TRACKED_CHAT_ID
    )


async def send_lines(update, lines):
    """
    Telegram messages have a character limit. This sends long reports
    in safe separate messages.
    """
    current_message = ""

    for line in lines:
        next_message = f"{current_message}\n{line}".strip()

        if len(next_message) > 3500:
            await update.effective_message.reply_text(current_message)
            current_message = line
        else:
            current_message = next_message

    if current_message:
        await update.effective_message.reply_text(current_message)


async def start_command(update, context):
    await update.effective_message.reply_text(
        "Attendance checker is active.\n\n"
        "Commands:\n"
        "/mytime — your attendance today\n"
        "/today — today’s attendance report\n"
        "/week — this week’s attendance report"
    )


async def mytime_command(update, context):
    if not is_tracked_group(update):
        return

    user_id = update.effective_user.id
    start_time, end_time = today_range()
    totals = attendance_totals(start_time, end_time)

    if user_id not in totals:
        await update.effective_message.reply_text(
            "You do not have any recorded attendance today."
        )
        return

    person = totals[user_id]

    await update.effective_message.reply_text(
        f"{person['name']}, your attendance today is "
        f"{format_duration(person['seconds'])}."
    )


async def today_command(update, context):
    if not is_tracked_group(update):
        return

    start_time, end_time = today_range()
    totals = attendance_totals(start_time, end_time)

    if not totals:
        await update.effective_message.reply_text(
            "No attendance has been recorded today."
        )
        return

    ordered_totals = sorted(
        totals.values(),
        key=lambda person: person["seconds"],
        reverse=True,
    )

    lines = [
        "Today’s Attendance Report",
        "Timezone: Africa/Lagos",
        "",
    ]

    for number, person in enumerate(ordered_totals, start=1):
        lines.append(
            f"{number}. {person['name']} — "
            f"{format_duration(person['seconds'])}"
        )

    await send_lines(update, lines)


async def week_command(update, context):
    if not is_tracked_group(update):
        return

    start_time, end_time = week_range()
    totals = attendance_totals(start_time, end_time)

    if not totals:
        await update.effective_message.reply_text(
            "No attendance has been recorded this week."
        )
        return

    ordered_totals = sorted(
        totals.values(),
        key=lambda person: person["seconds"],
        reverse=True,
    )

    lines = [
        "This Week’s Attendance Report",
        "Timezone: Africa/Lagos",
        "",
    ]

    for number, person in enumerate(ordered_totals, start=1):
        lines.append(
            f"{number}. {person['name']} — "
            f"{format_duration(person['seconds'])}"
        )

    await send_lines(update, lines)


def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("mytime", mytime_command))
    application.add_handler(CommandHandler(["today", "report"], today_command))
    application.add_handler(CommandHandler(["week", "weekly"], week_command))

    print("Report bot is running.")
    print("Commands: /mytime, /today, /week")

    application.run_polling()


if __name__ == "__main__":
    main()