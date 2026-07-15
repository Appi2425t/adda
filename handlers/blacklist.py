"""Blacklist management — prevent the bot from sending to certain chats."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from db import get_pool
from handlers.admin import check_admin

logger = logging.getLogger(__name__)


async def blacklist_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a chat to the blacklist. /blacklistadd <chat_id> [reason]"""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /blacklistadd <chat_id> [reason]")
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid chat ID.")
        return

    reason = " ".join(context.args[1:]) if len(context.args) > 1 else None
    user_id = update.effective_user.id

    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM blacklist WHERE chat_id = $1", chat_id
        )
        if existing:
            await update.message.reply_text(f"Chat {chat_id} is already blacklisted.")
            return
        await conn.execute(
            "INSERT INTO blacklist (chat_id, reason, added_by) VALUES ($1, $2, $3)",
            chat_id, reason, user_id,
        )

    await update.message.reply_text(
        f"Chat {chat_id} blacklisted." + (f"\nReason: {reason}" if reason else "")
    )


async def blacklist_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a chat from the blacklist. /unblacklist <chat_id>"""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /unblacklist <chat_id>")
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid chat ID.")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM blacklist WHERE chat_id = $1", chat_id
        )

    if result == "DELETE 0":
        await update.message.reply_text(f"Chat {chat_id} is not blacklisted.")
    else:
        await update.message.reply_text(f"Chat {chat_id} removed from blacklist.")


async def blacklist_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all blacklisted chats. /blacklist"""
    if not await check_admin(update):
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, reason, added_at FROM blacklist ORDER BY added_at DESC"
        )

    if not rows:
        await update.message.reply_text("No chats are blacklisted.")
        return

    lines = ["Blacklisted chats:\n"]
    for r in rows:
        lines.append(
            f"Chat: {r['chat_id']} | Reason: {r['reason'] or 'N/A'} | Added: {r['added_at']:%Y-%m-%d}"
        )

    await update.message.reply_text("\n".join(lines))
