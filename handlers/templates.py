"""Message templates — save and reuse frequently sent messages."""

import logging

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from db import get_pool
from handlers.admin import check_admin

logger = logging.getLogger(__name__)

WAITING_TEMPLATE_NAME, WAITING_TEMPLATE_CONTENT = range(2)


async def template_save_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start /savetemplate conversation."""
    if not await check_admin(update):
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "Saving a new template.\n\n"
        "Send a name for this template (short, no spaces):"
    )
    return WAITING_TEMPLATE_NAME


async def template_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle template name."""
    name = update.message.text.strip()
    if not name or len(name) > 50:
        await update.message.reply_text("Name must be 1-50 characters. Try again:")
        return WAITING_TEMPLATE_NAME

    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM templates WHERE name = $1", name
        )
    if existing:
        await update.message.reply_text(
            f"Template '{name}' already exists. Send a different name:"
        )
        return WAITING_TEMPLATE_NAME

    context.user_data["template_name"] = name
    await update.message.reply_text(
        f"Name: {name}\n\n"
        "Now send the template content.\n"
        "Supports: text, photo with caption, document with caption."
    )
    return WAITING_TEMPLATE_CONTENT


async def template_content_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle template content."""
    msg = update.message

    if msg.photo:
        mtype = "photo"
        file_id = msg.photo[-1].file_id
        content = msg.caption or ""
    elif msg.document:
        mtype = "document"
        file_id = msg.document.file_id
        content = msg.caption or ""
    elif msg.text:
        mtype = "text"
        file_id = None
        content = msg.text
    else:
        await msg.reply_text("Unsupported. Send text, photo, or document.")
        return WAITING_TEMPLATE_CONTENT

    name = context.user_data["template_name"]
    user_id = update.effective_user.id

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO templates (name, content_text, message_type, file_id, created_by)
               VALUES ($1, $2, $3, $4, $5)""",
            name, content, mtype, file_id, user_id,
        )

    await update.message.reply_text(f"Template '{name}' saved!")
    context.user_data.clear()
    return ConversationHandler.END


async def template_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all saved templates. /templates"""
    if not await check_admin(update):
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, message_type, content_text FROM templates ORDER BY name"
        )

    if not rows:
        await update.message.reply_text("No templates saved yet. Use /savetemplate to create one.")
        return

    lines = ["Saved Templates:\n"]
    for r in rows:
        preview = (r["content_text"][:40] + "...") if r["content_text"] and len(r["content_text"]) > 40 else (r["content_text"] or "(media)")
        lines.append(f"#{r['id']} {r['name']} [{r['message_type']}] — {preview}")

    lines.append("\nUse /usetemplate <name> before /schedule to apply a template.")
    await update.message.reply_text("\n".join(lines))


async def template_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Load a template into user_data for the next /schedule. /usetemplate <name>"""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /usetemplate <name>")
        return

    name = context.args[0]
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM templates WHERE name = $1", name)

    if not row:
        await update.message.reply_text(f"Template '{name}' not found.")
        return

    context.user_data["active_template"] = dict(row)
    preview = (row["content_text"][:100] + "...") if row["content_text"] and len(row["content_text"]) > 100 else (row["content_text"] or "(media)")

    await update.message.reply_text(
        f"Template '{name}' loaded!\n\n"
        f"Content: {preview}\n\n"
        f"Now run /schedule — the template content will be used."
    )


async def template_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a template. /deletetemplate <name>"""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /deletetemplate <name>")
        return

    name = context.args[0]
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM templates WHERE name = $1", name)

    if result == "DELETE 0":
        await update.message.reply_text(f"Template '{name}' not found.")
    else:
        await update.message.reply_text(f"Template '{name}' deleted.")


async def template_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Template creation cancelled.")
    return ConversationHandler.END


def get_template_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("savetemplate", template_save_start)],
        states={
            WAITING_TEMPLATE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, template_name_received),
            ],
            WAITING_TEMPLATE_CONTENT: [
                MessageHandler(filters.PHOTO, template_content_received),
                MessageHandler(filters.Document.ALL, template_content_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, template_content_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", template_cancel)],
    )
