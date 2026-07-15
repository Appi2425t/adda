"""Broadcast handler — send a message to multiple targets at once."""

import logging
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from db import get_pool
from handlers.admin import check_admin

logger = logging.getLogger(__name__)

WAITING_BROADCAST_TARGETS, WAITING_BROADCAST_CONTENT, WAITING_BROADCAST_OPTIONS = range(3)


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start /broadcast conversation."""
    if not await check_admin(update):
        return ConversationHandler.END

    context.user_data.clear()

    pool = get_pool()
    async with pool.acquire() as conn:
        targets = await conn.fetch("SELECT id, chat_id, label FROM targets ORDER BY label")

    if not targets:
        await update.message.reply_text(
            "No saved targets found. Add targets first with /addtarget, then try /broadcast again."
        )
        return ConversationHandler.END

    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("All saved targets", callback_data="bcast:all")]
    ]
    for t in targets:
        keyboard.append([
            InlineKeyboardButton(
                f"{t['label']} ({t['chat_id']})",
                callback_data=f"bcast:{t['chat_id']}",
            )
        ])

    await update.message.reply_text(
        "Broadcast: Send the same message to multiple targets.\n\n"
        "Select which targets to broadcast to:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAITING_BROADCAST_TARGETS


async def broadcast_targets_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle target selection."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "bcast:all":
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT chat_id FROM targets")
        context.user_data["broadcast_targets"] = [r["chat_id"] for r in rows]
    else:
        chat_id = int(data.split(":")[1])
        context.user_data["broadcast_targets"] = [chat_id]

    count = len(context.user_data["broadcast_targets"])
    await query.edit_message_text(
        f"Selected {count} target(s).\n\n"
        "Now send the message content to broadcast.\n"
        "Supports: text, photo with caption, document with caption.\n\n"
        "Send /cancel to abort.",
    )
    return WAITING_BROADCAST_CONTENT


async def broadcast_content_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast content."""
    msg = update.message

    if msg.photo:
        context.user_data["broadcast_type"] = "photo"
        context.user_data["broadcast_file_id"] = msg.photo[-1].file_id
        context.user_data["broadcast_text"] = msg.caption or ""
    elif msg.document:
        context.user_data["broadcast_type"] = "document"
        context.user_data["broadcast_file_id"] = msg.document.file_id
        context.user_data["broadcast_text"] = msg.caption or ""
    elif msg.text:
        context.user_data["broadcast_type"] = "text"
        context.user_data["broadcast_file_id"] = None
        context.user_data["broadcast_text"] = msg.text
    else:
        await msg.reply_text("Unsupported content. Send text, photo, or document.")
        return WAITING_BROADCAST_CONTENT

    keyboard = [
        [InlineKeyboardButton("Send now", callback_data="bcast:send")],
        [InlineKeyboardButton("Cancel", callback_data="bcast:cancel")],
    ]

    targets = context.user_data["broadcast_targets"]
    preview = (context.user_data["broadcast_text"][:100] + "...") if context.user_data["broadcast_text"] and len(context.user_data["broadcast_text"]) > 100 else (context.user_data["broadcast_text"] or "(media)")

    await msg.reply_text(
        f"Broadcast preview:\n\n"
        f"Targets: {len(targets)} chat(s)\n"
        f"Content: {preview}\n\n"
        f"Confirm?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAITING_BROADCAST_OPTIONS


async def broadcast_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the broadcast."""
    query = update.callback_query
    await query.answer()

    if query.data == "bcast:cancel":
        await query.edit_message_text("Broadcast cancelled.")
        context.user_data.clear()
        return ConversationHandler.END

    targets = context.user_data["broadcast_targets"]
    btype = context.user_data["broadcast_type"]
    text = context.user_data["broadcast_text"]
    file_id = context.user_data.get("broadcast_file_id")

    application = context.application
    bot = application.bot
    pool = get_pool()

    success = 0
    failed = 0
    errors: list[str] = []

    for chat_id in targets:
        # Blacklist check
        async with pool.acquire() as conn:
            bl = await conn.fetchrow(
                "SELECT id FROM blacklist WHERE chat_id = $1", chat_id
            )
        if bl:
            errors.append(f"{chat_id}: blacklisted")
            failed += 1
            continue

        try:
            if btype == "photo" and file_id:
                await bot.send_photo(chat_id=chat_id, photo=file_id, caption=text)
            elif btype == "document" and file_id:
                await bot.send_document(chat_id=chat_id, document=file_id, caption=text)
            else:
                await bot.send_message(chat_id=chat_id, text=text)
            success += 1

            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO send_log (schedule_id, target_chat_id, status)
                       VALUES (NULL, $1, 'success')""",
                    chat_id,
                )
        except Exception as e:
            failed += 1
            errors.append(f"{chat_id}: {e}")
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO send_log (schedule_id, target_chat_id, status, error_message)
                       VALUES (NULL, $1, 'failed', $2)""",
                    chat_id, str(e),
                )

        # Rate limit between targets
        time.sleep(1.0)

    result = f"Broadcast complete!\n\nSent: {success}\nFailed: {failed}"
    if errors:
        result += "\n\nErrors:\n" + "\n".join(errors[:10])
        if len(errors) > 10:
            result += f"\n... and {len(errors) - 10} more"

    await query.edit_message_text(result)
    context.user_data.clear()
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Broadcast cancelled.")
    return ConversationHandler.END


def get_broadcast_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            WAITING_BROADCAST_TARGETS: [
                CallbackQueryHandler(broadcast_targets_selected, pattern=r"^bcast:"),
            ],
            WAITING_BROADCAST_CONTENT: [
                MessageHandler(filters.PHOTO, broadcast_content_received),
                MessageHandler(filters.Document.ALL, broadcast_content_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_content_received),
                CommandHandler("cancel", broadcast_cancel),
            ],
            WAITING_BROADCAST_OPTIONS: [
                CallbackQueryHandler(broadcast_confirmed, pattern=r"^bcast:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    )
