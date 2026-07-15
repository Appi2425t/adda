"""Miscellaneous handlers: /start, /help, /stats, /cleanup."""

import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from db import get_pool
from handlers.admin import is_admin, check_admin

logger = logging.getLogger(__name__)

CLIST_TEXT = """All Commands & Usage

SCHEDULES
─────────────────────────────────────────
/schedule              Create a new scheduled message (interactive flow)
/schedules             List all active schedules
/edit <id>             Edit an existing schedule's content, target, or time
/cancel <id>           Cancel (deactivate) a schedule
/pause <id>            Pause a schedule without deleting it
/resume <id>           Resume a paused schedule
/preview <id>          Preview what a schedule will send
/test <id>             Send a test message to verify bot can reach the target
/next                  Show the next 10 upcoming scheduled sends

BROADCAST & TEMPLATES
─────────────────────────────────────────
/broadcast             Send the same message to multiple targets at once
/savetemplate          Save a reusable message template (interactive)
/templates             List all saved templates
/usetemplate <name>    Load a template — its content is used in the next /schedule
/deletetemplate <name> Delete a saved template

TARGETS
─────────────────────────────────────────
/addtarget <id> <label>   Save a chat/channel/group as a named target
/removetarget <label>     Remove a saved target by label
/targets                  List all saved targets

SAFETY
─────────────────────────────────────────
/blacklistadd <id> [reason]  Block the bot from sending to a chat
/unblacklist <id>            Unblock a chat
/blacklist                   List all blacklisted chats
/duplicates                  Find schedules with same target + content

DATA
─────────────────────────────────────────
/export                     Export all active schedules as a JSON file
/import                     Import schedules from a JSON file (attach .json)
/logs [hours]               View send history (default: last 24h)
/cleanup [days]             Delete old send logs (default: older than 30 days)

INFO
─────────────────────────────────────────
/whoami                     Get your Telegram user ID (for ADMIN_IDS setup)
/stats                      View detailed bot statistics
/help                       Show categorized help text
/clist                      Show this full command list with usage
/start                      Welcome message

EXAMPLES
─────────────────────────────────────────
/addtarget -100123456 main_channel
/blacklistadd -10099999 spam_group bots are not welcome
/savetemplate              (then send a name, then the message)
/usetemplate daily_news   (then run /schedule to use it)
/logs 48                   (show last 48 hours of send history)
/cleanup 7                 (delete logs older than 7 days)
"""


HELP_TEXT = """Telegram Auto-Scheduler Bot — Commands

Scheduling:
/schedule — Create a new scheduled message (interactive)
/schedules — List all active schedules
/edit <id> — Edit an existing schedule
/cancel <id> — Cancel a schedule
/pause <id> — Pause a schedule
/resume <id> — Resume a paused schedule
/preview <id> — Preview what a schedule sends
/test <id> — Send a test message to verify delivery
/next — Show next 10 upcoming sends

Broadcast & Templates:
/broadcast — Send a message to multiple targets
/savetemplate — Save a reusable message template
/templates — List saved templates
/usetemplate <name> — Load a template for /schedule
/deletetemplate <name> — Delete a template

Targets:
/addtarget <chat_id> <label> — Save a target chat
/removetarget <label> — Remove a saved target
/targets — List all saved targets

Safety:
/blacklistadd <chat_id> [reason] — Block bot from a chat
/unblacklist <chat_id> — Unblock a chat
/blacklist — List blacklisted chats
/duplicates — Check for duplicate schedules

Data:
/export — Export all schedules as JSON
/import — Import schedules from a JSON file (reply to file)
/logs [hours] — View send history (default: 24h)
/cleanup [days] — Delete send logs older than N days (default: 30)

Info:
/whoami — Get your Telegram user ID
/stats — View detailed statistics
/help — Show this help message
/clist — Show full command list with usage & examples
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not await check_admin(update):
        return
    await update.message.reply_text(
        "Welcome to the Telegram Auto-Scheduler Bot!\n\n"
        "Schedule messages to be sent automatically — one-time or recurring.\n"
        "Supports text, photos, documents, inline buttons, and multi-target broadcast.\n\n"
        "Use /help to see all available commands."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    if not await check_admin(update):
        return
    await update.message.reply_text(HELP_TEXT)


async def clist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clist command — show full command list with usage and examples."""
    if not await check_admin(update):
        return
    await update.message.reply_text(CLIST_TEXT)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed message statistics."""
    if not await check_admin(update):
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        total_sent = await conn.fetchval("SELECT COUNT(*) FROM send_log WHERE status = 'success'")
        total_failed = await conn.fetchval("SELECT COUNT(*) FROM send_log WHERE status = 'failed'")
        active_schedules = await conn.fetchval(
            "SELECT COUNT(*) FROM schedules WHERE is_active = TRUE AND is_paused = FALSE"
        )
        paused_schedules = await conn.fetchval(
            "SELECT COUNT(*) FROM schedules WHERE is_active = TRUE AND is_paused = TRUE"
        )
        total_targets = await conn.fetchval("SELECT COUNT(*) FROM targets")
        total_templates = await conn.fetchval("SELECT COUNT(*) FROM templates")
        total_blacklisted = await conn.fetchval("SELECT COUNT(*) FROM blacklist")

        now = datetime.now(timezone.utc)
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)

        sent_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM send_log WHERE status = 'success' AND sent_at > $1", last_24h
        )
        sent_7d = await conn.fetchval(
            "SELECT COUNT(*) FROM send_log WHERE status = 'success' AND sent_at > $1", last_7d
        )
        failed_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM send_log WHERE status = 'failed' AND sent_at > $1", last_24h
        )

        type_rows = await conn.fetch(
            """SELECT schedule_type, COUNT(*) as cnt
               FROM schedules WHERE is_active = TRUE
               GROUP BY schedule_type ORDER BY cnt DESC"""
        )

        target_rows = await conn.fetch(
            """SELECT target_chat_id, target_label, COUNT(*) as cnt
               FROM send_log WHERE status = 'success' AND sent_at > $1
               GROUP BY target_chat_id, target_label
               ORDER BY cnt DESC LIMIT 5""",
            last_7d,
        )

    text = (
        f"Bot Statistics\n"
        f"{'=' * 30}\n\n"
        f"Schedules:\n"
        f"  Active: {active_schedules}\n"
        f"  Paused: {paused_schedules}\n\n"
        f"Messages Sent:\n"
        f"  Total: {total_sent}\n"
        f"  Last 24h: {sent_24h}\n"
        f"  Last 7 days: {sent_7d}\n\n"
        f"Failed:\n"
        f"  Total: {total_failed}\n"
        f"  Last 24h: {failed_24h}\n\n"
        f"Infrastructure:\n"
        f"  Saved targets: {total_targets}\n"
        f"  Templates: {total_templates}\n"
        f"  Blacklisted chats: {total_blacklisted}\n"
    )

    if type_rows:
        text += "\nSchedule Types:\n"
        for r in type_rows:
            text += f"  {r['schedule_type']}: {r['cnt']}\n"

    if target_rows:
        text += "\nMost Active Targets (7d):\n"
        for r in target_rows:
            label = r["target_label"] or str(r["target_chat_id"])
            text += f"  {label}: {r['cnt']} messages\n"

    await update.message.reply_text(text)


async def cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete old send logs. /cleanup [days]"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return

    days = 30
    if context.args:
        try:
            days = int(context.args[0])
            days = max(1, min(days, 365))
        except ValueError:
            pass

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM send_log WHERE sent_at < $1", cutoff
        )
        count = int(result.split()[-1])

    await update.message.reply_text(f"Cleaned up {count} log entries older than {days} days.")
