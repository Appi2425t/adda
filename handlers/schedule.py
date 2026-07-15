"""Schedule creation conversation handler.

Interactive flow: target → content → schedule type → datetime → timezone → save.
"""

import logging
from datetime import datetime

import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import DEFAULT_TIMEZONE
from db import get_pool
from scheduler import add_job
from handlers.admin import check_admin

logger = logging.getLogger(__name__)

# Conversation states
WAITING_TARGET, WAITING_CONTENT, WAITING_SCHEDULE_TYPE, WAITING_DATETIME, WAITING_TIMEZONE = range(5)


async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the /schedule conversation."""
    if not await check_admin(update):
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "Let's create a new scheduled message.\n\n"
        "Where should this message be sent?"
    )

    pool = get_pool()
    async with pool.acquire() as conn:
        targets = await conn.fetch("SELECT id, chat_id, label FROM targets ORDER BY label")

    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Send to current chat", callback_data="target:current")]
    ]

    for t in targets:
        keyboard.append([
            InlineKeyboardButton(
                f"{t['label']} (ID: {t['chat_id']})",
                callback_data=f"target:{t['chat_id']}",
            )
        ])

    keyboard.append([InlineKeyboardButton("Add a new target", callback_data="target:new")])

    await update.message.reply_text(
        "Select a target chat:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAITING_TARGET


async def target_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle target selection callback."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "target:current":
        context.user_data["target_chat_id"] = query.message.chat_id
        context.user_data["target_label"] = None
    elif data == "target:new":
        await query.edit_message_text(
            "Send me the chat ID.\n"
            "You can use @userinfobot or @getmyid_bot to find chat IDs."
        )
        return WAITING_TARGET
    else:
        chat_id = int(data.split(":")[1])
        context.user_data["target_chat_id"] = chat_id
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT label FROM targets WHERE chat_id = $1", chat_id
            )
        context.user_data["target_label"] = row["label"] if row else None

    await query.edit_message_text(
        f"Target set to `{context.user_data['target_chat_id']}`\n\n"
        "Now, send me the message content.\n"
        "You can send:\n"
        "- Text message\n"
        "- Photo with caption\n"
        "- Document with caption\n\n"
        "Or send /cancel to abort.",
        parse_mode="Markdown",
    )
    return WAITING_CONTENT


async def target_new_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle manual chat ID input for new target."""
    text = update.message.text
    try:
        chat_id = int(text.strip())
    except ValueError:
        await update.message.reply_text("Please enter a valid numeric chat ID.")
        return WAITING_TARGET

    context.user_data["target_chat_id"] = chat_id
    context.user_data["target_label"] = None

    await update.message.reply_text(
        f"Target set to `{chat_id}`\n\n"
        "Now, send me the message content.",
        parse_mode="Markdown",
    )
    return WAITING_CONTENT


async def content_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle received content (text, photo, or document)."""
    msg = update.message

    if msg.photo:
        context.user_data["message_type"] = "photo"
        context.user_data["file_id"] = msg.photo[-1].file_id
        context.user_data["content_text"] = msg.caption or ""
    elif msg.document:
        context.user_data["message_type"] = "document"
        context.user_data["file_id"] = msg.document.file_id
        context.user_data["content_text"] = msg.caption or ""
    elif msg.text:
        context.user_data["message_type"] = "text"
        context.user_data["file_id"] = None
        context.user_data["content_text"] = msg.text
    else:
        await msg.reply_text("Unsupported content type. Send text, photo, or document.")
        return WAITING_CONTENT

    keyboard = [
        [
            InlineKeyboardButton("One-time", callback_data="type:once"),
            InlineKeyboardButton("Daily", callback_data="type:daily"),
        ],
        [
            InlineKeyboardButton("Weekly", callback_data="type:weekly"),
            InlineKeyboardButton("Interval", callback_data="type:interval"),
        ],
        [
            InlineKeyboardButton("Custom Cron", callback_data="type:cron"),
        ],
    ]

    await msg.reply_text(
        "Content received!\n\nSelect schedule type:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAITING_SCHEDULE_TYPE


async def schedule_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle schedule type selection."""
    query = update.callback_query
    await query.answer()

    stype = query.data.split(":")[1]
    context.user_data["schedule_type"] = stype

    if stype == "once":
        await query.edit_message_text(
            "Send the date and time for this one-time message.\n"
            "Format: `YYYY-MM-DD HH:MM` (e.g. `2025-01-15 14:30`)",
            parse_mode="Markdown",
        )
        return WAITING_DATETIME

    elif stype in ("daily", "weekly"):
        await query.edit_message_text(
            "Send the time for this recurring message.\n"
            "Format: `HH:MM` (e.g. `09:30`)",
            parse_mode="Markdown",
        )
        return WAITING_DATETIME

    elif stype == "interval":
        await query.edit_message_text(
            "Send the interval in minutes (e.g. `30` for every 30 minutes).",
            parse_mode="Markdown",
        )
        return WAITING_DATETIME

    elif stype == "cron":
        await query.edit_message_text(
            "Send a cron expression.\n"
            "Format: `minute hour day month day_of_week`\n"
            "Example: `0 9 * * 1-5` (weekdays at 9am)",
            parse_mode="Markdown",
        )
        return WAITING_DATETIME


async def datetime_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle datetime/timezone input."""
    text = update.message.text.strip()
    stype = context.user_data["schedule_type"]

    tz_name = DEFAULT_TIMEZONE
    try:
        tz = pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        tz = pytz.UTC

    if stype == "once":
        try:
            run_at = datetime.strptime(text, "%Y-%m-%d %H:%M")
            run_at = tz.localize(run_at)
        except ValueError:
            await update.message.reply_text(
                "Invalid format. Use `YYYY-MM-DD HH:MM` (e.g. `2025-01-15 14:30`)",
                parse_mode="Markdown",
            )
            return WAITING_DATETIME
        context.user_data["run_at"] = run_at
        context.user_data["cron_expression"] = None

    elif stype in ("daily", "weekly"):
        try:
            parts = text.split(":")
            hour, minute = int(parts[0]), int(parts[1])
            now = datetime.now(tz)
            run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_at <= now:
                run_at = run_at.replace(day=run_at.day + 1)
        except (ValueError, IndexError):
            await update.message.reply_text(
                "Invalid format. Use `HH:MM` (e.g. `09:30`)",
                parse_mode="Markdown",
            )
            return WAITING_DATETIME
        context.user_data["run_at"] = run_at
        context.user_data["cron_expression"] = None

    elif stype == "interval":
        try:
            minutes = int(text)
            if minutes <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive number of minutes.")
            return WAITING_DATETIME
        context.user_data["run_at"] = None
        context.user_data["cron_expression"] = f"*/{minutes} * * * *"

    elif stype == "cron":
        fields = text.split()
        if len(fields) != 5:
            await update.message.reply_text(
                "Cron expression must have 5 fields.\n"
                "Format: `minute hour day month day_of_week`",
                parse_mode="Markdown",
            )
            return WAITING_DATETIME
        context.user_data["run_at"] = None
        context.user_data["cron_expression"] = text

    # Ask for timezone confirmation
    await update.message.reply_text(
        f"Timezone: `{tz_name}` (from DEFAULT_TIMEZONE)\n"
        "Send a different timezone to override, or /skip to use this.",
        parse_mode="Markdown",
    )
    return WAITING_TIMEZONE


async def timezone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone input or skip."""
    text = update.message.text.strip()

    if text == "/skip":
        tz_name = DEFAULT_TIMEZONE
    else:
        try:
            pytz.timezone(text)
            tz_name = text
        except pytz.exceptions.UnknownTimeZoneError:
            await update.message.reply_text(
                f"Unknown timezone `{text}`. Try again or /skip.",
                parse_mode="Markdown",
            )
            return WAITING_TIMEZONE

    context.user_data["timezone"] = tz_name

    # Save to database
    pool = get_pool()
    ud = context.user_data

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO schedules
               (created_by, target_chat_id, target_label, message_type,
                content_text, file_id, schedule_type, cron_expression,
                run_at, timezone)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               RETURNING id, *""",
            update.effective_user.id,
            ud["target_chat_id"],
            ud.get("target_label"),
            ud["message_type"],
            ud["content_text"],
            ud.get("file_id"),
            ud["schedule_type"],
            ud.get("cron_expression"),
            ud.get("run_at"),
            tz_name,
        )

    # Add to APScheduler
    add_job(row["id"], dict(row))

    # Update next_run_at
    from scheduler import scheduler as sched

    job = sched.get_job(f"schedule_{row['id']}") if sched else None
    next_run = job.next_run_time if job else None
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE schedules SET next_run_at = $1 WHERE id = $2",
            next_run, row["id"],
        )

    preview = ud["content_text"][:100] if ud["content_text"] else "(media)"
    await update.message.reply_text(
        f"Schedule created! (ID: {row['id']})\n\n"
        f"Target: `{ud['target_chat_id']}`\n"
        f"Type: {ud['schedule_type']}\n"
        f"Content: {preview}\n"
        f"Timezone: {tz_name}\n"
        f"Next run: {next_run or 'immediate'}",
        parse_mode="Markdown",
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation."""
    context.user_data.clear()
    await update.message.reply_text("Schedule creation cancelled.")
    return ConversationHandler.END


def get_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("schedule", schedule_start)],
        states={
            WAITING_TARGET: [
                CallbackQueryHandler(target_selected, pattern=r"^target:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, target_new_input),
            ],
            WAITING_CONTENT: [
                MessageHandler(filters.PHOTO, content_received),
                MessageHandler(filters.Document.ALL, content_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, content_received),
                CommandHandler("cancel", cancel),
            ],
            WAITING_SCHEDULE_TYPE: [
                CallbackQueryHandler(schedule_type_selected, pattern=r"^type:"),
            ],
            WAITING_DATETIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, datetime_received),
            ],
            WAITING_TIMEZONE: [
                MessageHandler(filters.TEXT, timezone_received),
                CommandHandler("skip", timezone_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
