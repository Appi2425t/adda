"""Edit an existing schedule — modify content, target, or timing."""

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
from scheduler import add_job, remove_job
from handlers.admin import check_admin

logger = logging.getLogger(__name__)

EDIT_CHOOSING, EDIT_CONTENT, EDIT_TIME = range(3)


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start /edit <id> conversation."""
    if not await check_admin(update):
        return ConversationHandler.END

    if not context.args:
        await update.message.reply_text("Usage: /edit <schedule_id>")
        return ConversationHandler.END

    try:
        sid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid schedule ID.")
        return ConversationHandler.END

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM schedules WHERE id = $1 AND is_active = TRUE", sid
        )
    if not row:
        await update.message.reply_text(f"Schedule {sid} not found or inactive.")
        return ConversationHandler.END

    context.user_data["edit_id"] = sid
    context.user_data["edit_row"] = dict(row)

    keyboard = [
        [InlineKeyboardButton("Content", callback_data="edit:content")],
        [InlineKeyboardButton("Target chat", callback_data="edit:target")],
        [InlineKeyboardButton("Time / schedule", callback_data="edit:time")],
        [InlineKeyboardButton("Silent mode", callback_data="edit:silent")],
        [InlineKeyboardButton("Cancel", callback_data="edit:cancel")],
    ]

    preview = (row["content_text"][:80] + "...") if row["content_text"] and len(row["content_text"]) > 80 else (row["content_text"] or "(media)")

    await update.message.reply_text(
        f"Editing Schedule #{sid}\n\n"
        f"Current target: {row['target_label'] or row['target_chat_id']}\n"
        f"Type: {row['schedule_type']}\n"
        f"Content: {preview}\n"
        f"Silent: {'Yes' if row['silent'] else 'No'}\n\n"
        f"What would you like to change?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return EDIT_CHOOSING


async def edit_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle what to edit."""
    query = update.callback_query
    await query.answer()

    if query.data == "edit:cancel":
        context.user_data.clear()
        await query.edit_message_text("Edit cancelled.")
        return ConversationHandler.END

    choice = query.data.split(":")[1]
    context.user_data["edit_field"] = choice

    if choice == "content":
        await query.edit_message_text(
            "Send the new message content.\n"
            "Supports: text, photo with caption, document with caption.\n\n"
            "Send /skip to keep current content."
        )
        return EDIT_CONTENT

    elif choice == "target":
        pool = get_pool()
        async with pool.acquire() as conn:
            targets = await conn.fetch("SELECT chat_id, label FROM targets ORDER BY label")
        keyboard = []
        for t in targets:
            keyboard.append([
                InlineKeyboardButton(
                    f"{t['label']} ({t['chat_id']})",
                    callback_data=f"edit_t:{t['chat_id']}",
                )
            ])
        keyboard.append([InlineKeyboardButton("Enter chat ID manually", callback_data="edit_t:manual")])
        await query.edit_message_text("Select new target:", reply_markup=InlineKeyboardMarkup(keyboard))
        return EDIT_TIME

    elif choice == "time":
        stype = context.user_data["edit_row"]["schedule_type"]
        if stype == "once":
            await query.edit_message_text(
                "Send new date and time.\n"
                "Format: `YYYY-MM-DD HH:MM`\n"
                "Send /skip to keep current.",
                parse_mode="Markdown",
            )
        elif stype in ("daily", "weekly"):
            await query.edit_message_text(
                "Send new time.\n"
                "Format: `HH:MM`\n"
                "Send /skip to keep current.",
                parse_mode="Markdown",
            )
        elif stype == "interval":
            await query.edit_message_text(
                "Send new interval in minutes.\n"
                "Send /skip to keep current.",
            )
        elif stype == "cron":
            await query.edit_message_text(
                "Send new cron expression (5 fields).\n"
                "Send /skip to keep current.",
            )
        return EDIT_TIME

    elif choice == "silent":
        pool = get_pool()
        sid = context.user_data["edit_id"]
        current = context.user_data["edit_row"]["silent"]
        new_val = not current
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET silent = $1 WHERE id = $2", new_val, sid
            )
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", sid)
        if row:
            remove_job(sid)
            add_job(sid, dict(row))

        await query.edit_message_text(f"Silent mode: {'ON' if new_val else 'OFF'}")
        context.user_data.clear()
        return ConversationHandler.END


async def edit_content_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new content."""
    msg = update.message

    if msg.text and msg.text == "/skip":
        pass  # keep current
    elif msg.photo:
        context.user_data["new_message_type"] = "photo"
        context.user_data["new_file_id"] = msg.photo[-1].file_id
        context.user_data["new_content_text"] = msg.caption or ""
    elif msg.document:
        context.user_data["new_message_type"] = "document"
        context.user_data["new_file_id"] = msg.document.file_id
        context.user_data["new_content_text"] = msg.caption or ""
    elif msg.text:
        context.user_data["new_message_type"] = "text"
        context.user_data["new_file_id"] = None
        context.user_data["new_content_text"] = msg.text
    else:
        await msg.reply_text("Unsupported. Send text, photo, document, or /skip.")
        return EDIT_CONTENT

    sid = context.user_data["edit_id"]
    pool = get_pool()

    updates: list[str] = []
    params: list = []
    idx = 1

    if "new_content_text" in context.user_data:
        updates.append(f"content_text = ${idx}")
        params.append(context.user_data["new_content_text"])
        idx += 1
    if "new_message_type" in context.user_data:
        updates.append(f"message_type = ${idx}")
        params.append(context.user_data["new_message_type"])
        idx += 1
    if "new_file_id" in context.user_data:
        updates.append(f"file_id = ${idx}")
        params.append(context.user_data["new_file_id"])
        idx += 1

    if updates:
        params.append(sid)
        query = f"UPDATE schedules SET {', '.join(updates)} WHERE id = ${idx}"
        async with pool.acquire() as conn:
            await conn.execute(query, *params)

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", sid)
        if row:
            remove_job(sid)
            add_job(sid, dict(row))

    await update.message.reply_text(f"Schedule #{sid} content updated!")
    context.user_data.clear()
    return ConversationHandler.END


async def edit_target_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new target selection."""
    query = update.callback_query
    await query.answer()

    data = query.data
    sid = context.user_data["edit_id"]
    pool = get_pool()

    if data == "edit_t:manual":
        await query.edit_message_text("Send the new chat ID:")
        context.user_data["edit_field"] = "target_manual"
        return EDIT_TIME

    chat_id = int(data.split(":")[1])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT label FROM targets WHERE chat_id = $1", chat_id
        )
    label = row["label"] if row else None

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE schedules SET target_chat_id = $1, target_label = $2 WHERE id = $3",
            chat_id, label, sid,
        )

    async with pool.acquire() as conn:
        full_row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", sid)
    if full_row:
        remove_job(sid)
        add_job(sid, dict(full_row))

    await query.edit_message_text(f"Schedule #{sid} target updated to {chat_id}")
    context.user_data.clear()
    return ConversationHandler.END


async def edit_time_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new time/target_manual input."""
    text = update.message.text.strip()
    sid = context.user_data["edit_id"]
    pool = get_pool()

    # Handle manual target ID input
    if context.user_data.get("edit_field") == "target_manual":
        try:
            chat_id = int(text)
        except ValueError:
            await update.message.reply_text("Invalid chat ID. Try again.")
            return EDIT_TIME

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET target_chat_id = $1, target_label = NULL WHERE id = $2",
                chat_id, sid,
            )
        async with pool.acquire() as conn:
            full_row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", sid)
        if full_row:
            remove_job(sid)
            add_job(sid, dict(full_row))

        await update.message.reply_text(f"Schedule #{sid} target updated to {chat_id}")
        context.user_data.clear()
        return ConversationHandler.END

    # Handle time update
    if text == "/skip":
        await update.message.reply_text(f"Schedule #{sid} time unchanged.")
        context.user_data.clear()
        return ConversationHandler.END

    stype = context.user_data["edit_row"]["schedule_type"]
    tz_name = context.user_data["edit_row"].get("timezone", DEFAULT_TIMEZONE)
    try:
        tz = pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        tz = pytz.UTC

    if stype == "once":
        try:
            run_at = datetime.strptime(text, "%Y-%m-%d %H:%M")
            run_at = tz.localize(run_at)
        except ValueError:
            await update.message.reply_text("Invalid format. Use `YYYY-MM-DD HH:MM`", parse_mode="Markdown")
            return EDIT_TIME
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET run_at = $1 WHERE id = $2", run_at, sid,
            )
    elif stype in ("daily", "weekly"):
        try:
            parts = text.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            await update.message.reply_text("Invalid format. Use `HH:MM`", parse_mode="Markdown")
            return EDIT_TIME
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT run_at FROM schedules WHERE id = $1", sid)
            old = row["run_at"]
            if old:
                new_run = old.replace(hour=hour, minute=minute)
            else:
                new_run = datetime.now(tz).replace(hour=hour, minute=minute)
            await conn.execute(
                "UPDATE schedules SET run_at = $1 WHERE id = $2", new_run, sid,
            )
    elif stype == "interval":
        try:
            minutes = int(text)
            if minutes <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Enter a positive number of minutes.")
            return EDIT_TIME
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET cron_expression = $1 WHERE id = $2",
                f"*/{minutes} * * * *", sid,
            )
    elif stype == "cron":
        fields = text.split()
        if len(fields) != 5:
            await update.message.reply_text("Cron must have 5 fields. Try again or /skip.")
            return EDIT_TIME
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET cron_expression = $1 WHERE id = $2", text, sid,
            )

    # Re-register the job
    async with pool.acquire() as conn:
        full_row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", sid)
    if full_row:
        remove_job(sid)
        add_job(sid, dict(full_row))

    await update.message.reply_text(f"Schedule #{sid} timing updated!")
    context.user_data.clear()
    return ConversationHandler.END


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Edit cancelled.")
    return ConversationHandler.END


def get_edit_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_CHOOSING: [
                CallbackQueryHandler(edit_choice, pattern=r"^edit:"),
            ],
            EDIT_CONTENT: [
                MessageHandler(filters.PHOTO, edit_content_received),
                MessageHandler(filters.Document.ALL, edit_content_received),
                MessageHandler(filters.TEXT, edit_content_received),
            ],
            EDIT_TIME: [
                CallbackQueryHandler(edit_target_selected, pattern=r"^edit_t:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_time_received),
                CommandHandler("skip", edit_time_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel)],
    )
