import asyncio
import html
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


load_dotenv()

BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN")
tracked_chat_id = os.getenv("TRACKED_CHAT_ID")

# API.Bible settings for the NKJV feature.
# Create an API.Bible key, then place both values in your .env file.
BIBLE_API_KEY = os.getenv("BIBLE_API_KEY")
NKJV_BIBLE_ID = os.getenv("NKJV_BIBLE_ID")
BIBLE_API_BASE_URL = "https://rest.api.bible/v1"

BASE_FOLDER = os.path.dirname(os.path.abspath(__file__))
DATABASE_FILE = os.path.join(BASE_FOLDER, "attendance.db")
LAGOS_TIMEZONE = ZoneInfo("Africa/Lagos")

if not BOT_TOKEN or not tracked_chat_id:
    raise ValueError(
        "Your .env file must contain REPORT_BOT_TOKEN and TRACKED_CHAT_ID."
    )

TRACKED_CHAT_ID = int(tracked_chat_id)


# The codes below are the book identifiers used by API.Bible.
BOOK_ALIASES = {
    "genesis": "GEN", "gen": "GEN", "exodus": "EXO", "exo": "EXO",
    "leviticus": "LEV", "lev": "LEV", "numbers": "NUM", "num": "NUM",
    "deuteronomy": "DEU", "deut": "DEU", "joshua": "JOS", "josh": "JOS",
    "judges": "JDG", "judg": "JDG", "ruth": "RUT", "1 samuel": "1SA",
    "1 sam": "1SA", "2 samuel": "2SA", "2 sam": "2SA", "1 kings": "1KI",
    "1 kgs": "1KI", "2 kings": "2KI", "2 kgs": "2KI", "1 chronicles": "1CH",
    "1 chron": "1CH", "2 chronicles": "2CH", "2 chron": "2CH", "ezra": "EZR",
    "nehemiah": "NEH", "neh": "NEH", "esther": "EST", "est": "EST",
    "job": "JOB", "psalm": "PSA", "psalms": "PSA", "ps": "PSA",
    "proverbs": "PRO", "prov": "PRO", "ecclesiastes": "ECC", "ecc": "ECC",
    "song of solomon": "SNG", "song of songs": "SNG", "song": "SNG",
    "isaiah": "ISA", "isa": "ISA", "jeremiah": "JER", "jer": "JER",
    "lamentations": "LAM", "lam": "LAM", "ezekiel": "EZK", "ezek": "EZK",
    "daniel": "DAN", "dan": "DAN", "hosea": "HOS", "hos": "HOS",
    "joel": "JOL", "amos": "AMO", "obadiah": "OBA", "obad": "OBA",
    "jonah": "JON", "micah": "MIC", "mic": "MIC", "nahum": "NAM",
    "nah": "NAM", "habakkuk": "HAB", "hab": "HAB", "zephaniah": "ZEP",
    "zeph": "ZEP", "haggai": "HAG", "hag": "HAG", "zechariah": "ZEC",
    "zech": "ZEC", "malachi": "MAL", "mal": "MAL", "matthew": "MAT",
    "matt": "MAT", "mark": "MRK", "luke": "LUK", "john": "JHN", "jn": "JHN",
    "acts": "ACT", "romans": "ROM", "rom": "ROM", "1 corinthians": "1CO",
    "1 cor": "1CO", "2 corinthians": "2CO", "2 cor": "2CO", "galatians": "GAL",
    "gal": "GAL", "ephesians": "EPH", "eph": "EPH", "philippians": "PHP",
    "phil": "PHP", "colossians": "COL", "col": "COL", "1 thessalonians": "1TH",
    "1 thess": "1TH", "2 thessalonians": "2TH", "2 thess": "2TH",
    "1 timothy": "1TI", "1 tim": "1TI", "2 timothy": "2TI", "2 tim": "2TI",
    "titus": "TIT", "philemon": "PHM", "phlm": "PHM", "hebrews": "HEB",
    "heb": "HEB", "james": "JAS", "jas": "JAS", "1 peter": "1PE",
    "1 pet": "1PE", "2 peter": "2PE", "2 pet": "2PE", "1 john": "1JN",
    "2 john": "2JN", "3 john": "3JN", "jude": "JUD", "revelation": "REV",
    "revelations": "REV", "rev": "REV",
}


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
    """Returns total attendance for each person overlapping the requested period."""
    if not os.path.exists(DATABASE_FILE):
        return {}

    database = open_database()
    try:
        rows = database.execute("""
            SELECT user_id, display_name, joined_at,
                   COALESCE(left_at, last_seen_at) AS effective_left_at
            FROM attendance_sessions
            WHERE joined_at < ?
              AND COALESCE(left_at, last_seen_at) > ?
        """, (end_time.isoformat(), start_time.isoformat())).fetchall()
    finally:
        database.close()

    totals = defaultdict(lambda: {"name": "", "seconds": 0})
    for user_id, display_name, joined_at, left_at in rows:
        actual_start = max(datetime.fromisoformat(joined_at), start_time)
        actual_end = min(datetime.fromisoformat(left_at), end_time)
        seconds = max(0, int((actual_end - actual_start).total_seconds()))
        if seconds > 0:
            totals[user_id]["name"] = display_name
            totals[user_id]["seconds"] += seconds

    return dict(totals)


def today_range():
    now = datetime.now(LAGOS_TIMEZONE)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def week_range():
    now = datetime.now(LAGOS_TIMEZONE)
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_week = start_of_today - timedelta(days=now.weekday())
    return start_of_week.astimezone(timezone.utc), now.astimezone(timezone.utc)


def is_tracked_group(update):
    return update.effective_chat is not None and update.effective_chat.id == TRACKED_CHAT_ID


async def send_lines(update, lines):
    """Telegram messages have a character limit; split long reports safely."""
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


async def send_text_chunks(update, text, maximum_length=3500):
    """Send a long Bible chapter safely within Telegram's message limit."""
    while text:
        if len(text) <= maximum_length:
            await update.effective_message.reply_text(text)
            return

        split_at = text.rfind(" ", 0, maximum_length)
        if split_at == -1:
            split_at = maximum_length

        await update.effective_message.reply_text(text[:split_at].strip())
        text = text[split_at:].strip()


def parse_bible_reference(message_text):
    """Accepts examples such as 'John 3:16' and 'Romans 8:28-30'."""
    match = re.fullmatch(
        r"\s*(?P<book>(?:[1-3]\s*)?[A-Za-z]+(?:\s+[A-Za-z]+)?)\s+"
        r"(?P<chapter>\d{1,3})(?::(?P<first_verse>\d{1,3})"
        r"(?:\s*[-–]\s*(?P<last_verse>\d{1,3}))?)?\s*",
        message_text,
    )
    if not match:
        return None

    book_name = re.sub(r"\s+", " ", match.group("book").strip().lower())
    book_id = BOOK_ALIASES.get(book_name)
    if not book_id:
        return None

    chapter = match.group("chapter")
    first_verse = match.group("first_verse")
    last_verse = match.group("last_verse")

    if not first_verse:
        return f"{book_id}.{chapter}"
    if not last_verse:
        return f"{book_id}.{chapter}.{first_verse}"
    return f"{book_id}.{chapter}.{first_verse}-{book_id}.{chapter}.{last_verse}"


def clean_passage_text(content):
    content = re.sub(r"<sup[^>]*>.*?</sup>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<[^>]+>", "", content)
    return re.sub(r"\s+", " ", html.unescape(content)).strip()


def get_nkjv_passage(passage_id):
    """Runs in a background thread so the Telegram bot stays responsive."""
    url = (
        f"{BIBLE_API_BASE_URL}/bibles/{NKJV_BIBLE_ID}/passages/{passage_id}"
        "?content-type=html&include-notes=false&include-titles=false&include-chapter-numbers=false"
        "&include-verse-numbers=false"
    )
    request = Request(url, headers={"api-key": BIBLE_API_KEY, "accept": "application/json"})

    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code in (400, 404):
            return None, "I could not find that Bible reference. Try an example like John 3:16."
        if error.code in (401, 403):
            return None, "The NKJV Bible service key needs to be checked by the administrator."
        return None, "The Bible service is temporarily unavailable. Please try again shortly."
    except (URLError, TimeoutError, json.JSONDecodeError):
        return None, "I could not reach the Bible service. Please try again shortly."

    data = payload.get("data", {})
    passage_text = clean_passage_text(data.get("content", ""))
    reference = data.get("reference", "NKJV")
    if not passage_text:
        return None, "I could not find that Bible reference. Try an example like John 3:16."
    return (reference, passage_text), None


async def reply_with_bible_reference(update: Update, reference_text: str):
    """Look up one recognised Bible reference and send its NKJV response."""
    if not update.effective_message:
        return

    passage_id = parse_bible_reference(reference_text)
    if not passage_id:
        await update.effective_message.reply_text(
            "I could not recognise that Bible reference. Try an example like John 3:16."
        )
        return

    if not BIBLE_API_KEY or not NKJV_BIBLE_ID:
        await update.effective_message.reply_text(
            "Bible verses are not configured yet. The administrator needs to add "
            "BIBLE_API_KEY and NKJV_BIBLE_ID to the .env file."
        )
        return

    result, error_message = await asyncio.to_thread(get_nkjv_passage, passage_id)
    if error_message:
        await update.effective_message.reply_text(error_message)
        return

    reference, passage_text = result
    reply = f"{reference} (NKJV)\n\n{passage_text}\n\nNew King James Version®"
    await send_text_chunks(update, reply)


async def bible_verse_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply only when a group member sends a recognised Bible reference."""
    if not is_tracked_group(update) or not update.effective_message or not update.effective_message.text:
        return

    # Stay quiet for ordinary chat. Only answer messages that match a Bible reference.
    if not parse_bible_reference(update.effective_message.text):
        return

    await reply_with_bible_reference(update, update.effective_message.text)


async def verse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_tracked_group(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Use it like this: /verse John 3:16")
        return

    await reply_with_bible_reference(update, " ".join(context.args))


async def start_command(update, context):
    await update.effective_message.reply_text(
        "Attendance checker is active.\n\n"
        "Commands:\n"
        "/mytime — your attendance today\n"
        "/today — today’s attendance report\n"
        "/week — this week’s attendance report\n"
        "/verse John 3:16 — get a verse in NKJV\n\n"
        "You can also simply send: John 3:16"
    )


async def mytime_command(update, context):
    if not is_tracked_group(update):
        return
    start_time, end_time = today_range()
    totals = attendance_totals(start_time, end_time)
    user_id = update.effective_user.id
    if user_id not in totals:
        await update.effective_message.reply_text("You do not have any recorded attendance today.")
        return
    person = totals[user_id]
    await update.effective_message.reply_text(
        f"{person['name']}, your attendance today is {format_duration(person['seconds'])}."
    )


async def today_command(update, context):
    if not is_tracked_group(update):
        return
    start_time, end_time = today_range()
    totals = attendance_totals(start_time, end_time)
    if not totals:
        await update.effective_message.reply_text("No attendance has been recorded today.")
        return
    ordered_totals = sorted(totals.values(), key=lambda person: person["seconds"], reverse=True)
    lines = ["Today’s Attendance Report", "Timezone: Africa/Lagos", ""]
    for number, person in enumerate(ordered_totals, start=1):
        lines.append(f"{number}. {person['name']} — {format_duration(person['seconds'])}")
    await send_lines(update, lines)


async def week_command(update, context):
    if not is_tracked_group(update):
        return
    start_time, end_time = week_range()
    totals = attendance_totals(start_time, end_time)
    if not totals:
        await update.effective_message.reply_text("No attendance has been recorded this week.")
        return
    ordered_totals = sorted(totals.values(), key=lambda person: person["seconds"], reverse=True)
    lines = ["This Week’s Attendance Report", "Timezone: Africa/Lagos", ""]
    for number, person in enumerate(ordered_totals, start=1):
        lines.append(f"{number}. {person['name']} — {format_duration(person['seconds'])}")
    await send_lines(update, lines)


def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("mytime", mytime_command))
    application.add_handler(CommandHandler(["today", "report"], today_command))
    application.add_handler(CommandHandler(["week", "weekly"], week_command))
    application.add_handler(CommandHandler("verse", verse_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bible_verse_message))

    print("Report bot is running.")
    print("Commands: /mytime, /today, /week, /verse John 3:16")
    if not BIBLE_API_KEY or not NKJV_BIBLE_ID:
        print("Bible feature is waiting for BIBLE_API_KEY and NKJV_BIBLE_ID in .env.")

    application.run_polling()


if __name__ == "__main__":
    main()
