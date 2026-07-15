"""Admin and permission check handlers."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Check if a user ID is in the admin list."""
    return user_id in ADMIN_IDS


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return the caller's Telegram user ID and admin status."""
    user = update.effective_user
    await update.message.reply_text(
        f"Your user ID: `{user.id}`\n"
        f"Your username: @{user.username or 'N/A'}\n\n"
        f"{'You are an admin.' if is_admin(user.id) else 'You are NOT an admin.'}",
        parse_mode="Markdown",
    )


async def check_admin(update: Update) -> bool:
    """Check if the user is an admin. Reply with error if not. Returns True if admin."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "Access denied. Only admins can use this command.\n"
            "Run /whoami to get your user ID, then add it to ADMIN_IDS."
        )
        return False
    return True
